"""Cross-section rings: one swipe across a limb becomes a full loop around it.

Drawing a ring by hand means clicking around the back of the mesh, rotating
the view as you go — the most tedious gesture in organic retopo. A contour cut
replaces it: the stroke and the view direction span a plane, the plane's
intersection with the surface is chained into closed loops, and the loop
nearest the stroke is the ring the artist meant.

Pure numpy (SPEC §10): everything here takes triangle arrays, so the geometry
is testable without a viewport.
"""

from __future__ import annotations

import numpy as np


def cross_section(verts, tris, plane_pt, plane_n):
    """Intersect a triangle mesh with a plane. Returns a list of polylines.

    Closed loops come back with their last point equal to their first; open
    chains (the plane running off a boundary) just end. Marching-triangles:
    every triangle crossing the plane contributes one segment, and segments
    are chained by quantised endpoint identity — a shared edge interpolates to
    the same point from either side, so the chain closes without a weld pass.
    """
    verts = np.asarray(verts, dtype=float)
    tris = np.asarray(tris, dtype=int)
    n = np.asarray(plane_n, dtype=float)
    n = n / max(np.linalg.norm(n), 1e-12)
    s = (verts - np.asarray(plane_pt, dtype=float)) @ n

    scale = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0))) or 1.0
    quant = max(scale * 1e-7, 1e-12)

    def key(p):
        return (round(p[0] / quant), round(p[1] / quant), round(p[2] / quant))

    segments = []
    sv = s[tris]                                   # (m, 3) signed distances
    crossing = ~((sv > 0).all(axis=1) | (sv < 0).all(axis=1))
    for tri, d in zip(tris[crossing], sv[crossing]):
        pts = []
        for i in range(3):
            a, b = i, (i + 1) % 3
            da, db = d[a], d[b]
            if (da > 0) == (db > 0) and da != 0 and db != 0:
                continue
            if da == db:
                continue
            t = da / (da - db)
            if 0.0 <= t <= 1.0:
                pts.append(verts[tri[a]] + (verts[tri[b]] - verts[tri[a]]) * t)
        # dedupe vertices that sit exactly on the plane
        uniq = []
        for p in pts:
            if not any(np.linalg.norm(p - q) < quant for q in uniq):
                uniq.append(p)
        if len(uniq) == 2:
            segments.append((uniq[0], uniq[1]))

    if not segments:
        return []

    # chain by endpoint identity
    by_end = {}
    for i, (a, b) in enumerate(segments):
        by_end.setdefault(key(a), []).append((i, 0))
        by_end.setdefault(key(b), []).append((i, 1))

    used = [False] * len(segments)
    loops = []
    for start in range(len(segments)):
        if used[start]:
            continue
        used[start] = True
        a, b = segments[start]
        chain = [a, b]
        # extend forward from b, then backward from a
        for reverse in (False, True):
            while True:
                tip = chain[-1] if not reverse else chain[0]
                cands = [(i, e) for i, e in by_end.get(key(tip), [])
                         if not used[i]]
                if not cands:
                    break
                i, e = cands[0]
                used[i] = True
                nxt = segments[i][1 - e]
                if not reverse:
                    chain.append(nxt)
                else:
                    chain.insert(0, nxt)
        loops.append(np.asarray(chain, dtype=float))
    return loops


def is_closed(loop, tol=None):
    loop = np.asarray(loop, dtype=float)
    if len(loop) < 4:
        return False
    span = float(np.linalg.norm(loop.max(axis=0) - loop.min(axis=0))) or 1.0
    tol = tol if tol is not None else span * 1e-5
    return float(np.linalg.norm(loop[0] - loop[-1])) <= tol


def nearest_loop(loops, point, closed_only=True):
    """The loop whose closest point is nearest to `point`, or None.

    On a body, one plane cuts the leg you swiped *and* the torso behind it —
    the nearest loop is the one under the stroke, which is the one meant.
    """
    point = np.asarray(point, dtype=float)
    best = None
    for loop in loops:
        if closed_only and not is_closed(loop):
            continue
        d = float(np.linalg.norm(np.asarray(loop) - point, axis=1).min())
        if best is None or d < best[1]:
            best = (loop, d)
    return best[0] if best else None


def ring_segments(loop, k=4, samples_per_arc=8, start_at=None):
    """Cut a closed loop into k node positions and k sub-paths between them.

    Discovery treats a smooth closed ring as a quad region by cutting it into
    four sides, so emitting it as k arcs between k evenly spaced nodes hands
    it exactly the structure everything downstream already expects. If
    ``start_at`` is given, the first node lands at the loop point nearest it —
    so successive rings around the same limb get corresponding nodes, which is
    what makes bridging them by click-click segments pleasant.
    """
    loop = np.asarray(loop, dtype=float)
    if is_closed(loop):
        loop = loop[:-1]
    m = len(loop)
    if m < k:
        return None

    seg = np.linalg.norm(np.diff(np.vstack([loop, loop[:1]]), axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    if total <= 0:
        return None

    offset = 0
    if start_at is not None:
        offset = int(np.argmin(np.linalg.norm(
            loop - np.asarray(start_at, dtype=float), axis=1)))
    loop = np.roll(loop, -offset, axis=0)
    seg = np.roll(seg, -offset)
    cum = np.concatenate([[0.0], np.cumsum(seg)])

    def point_at(dist):
        dist = dist % total
        i = int(np.searchsorted(cum, dist, side="right") - 1)
        i = min(max(i, 0), m - 1)
        t = 0.0 if seg[i] <= 0 else (dist - cum[i]) / seg[i]
        return loop[i] + (loop[(i + 1) % m] - loop[i]) * t

    nodes = [point_at(total * j / k) for j in range(k)]
    paths = []
    for j in range(k):
        d0, d1 = total * j / k, total * (j + 1) / k
        pts = [point_at(d0 + (d1 - d0) * t / samples_per_arc)
               for t in range(samples_per_arc + 1)]
        pts[0] = nodes[j]
        pts[-1] = nodes[(j + 1) % k]
        paths.append(np.asarray(pts, dtype=float))
    return nodes, paths


def loop_perimeter(pts):
    pts = np.asarray(pts, dtype=float)
    return float(np.linalg.norm(
        np.diff(np.vstack([pts, pts[:1]]), axis=0), axis=1).sum())


def pair_rings(a_pts, b_pts):
    """Match the nodes of two rings one-to-one by proximity.

    Chain direction out of a cross-section is arbitrary, so index order cannot
    be trusted — ring B may wind the opposite way to ring A. Nearest-neighbour
    pairing is unambiguous for rings stacked along a limb, and demanding that
    it comes out bijective is the guard: if two of B's nodes want the same
    node of A, these are not corresponding rings and bridging them would fold.
    Returns a list of (i, j) index pairs, or None.
    """
    a = np.asarray(a_pts, dtype=float)
    b = np.asarray(b_pts, dtype=float)
    if len(a) != len(b) or len(a) == 0:
        return None
    pairs = []
    taken = set()
    for j, p in enumerate(b):
        d = np.linalg.norm(a - p, axis=1)
        i = int(np.argmin(d))
        if i in taken:
            return None
        taken.add(i)
        pairs.append((i, j))
    return pairs


def bridgeable(a_pts, b_pts, pairs, max_span_ratio=1.25):
    """Whether two paired rings are plausibly the same tube.

    The trap is two rings on *different* limbs — ring the left leg, ring the
    right, and an eager bridge would span the gap between them. Rings farther
    apart than their own circumference are not a tube segment; that single
    check keeps cross-limb bridges out while leaving any sane ladder spacing
    alone.
    """
    if not pairs:
        return False
    a = np.asarray(a_pts, dtype=float)
    b = np.asarray(b_pts, dtype=float)
    span = float(np.mean([np.linalg.norm(a[i] - b[j]) for i, j in pairs]))
    perim = min(loop_perimeter(a), loop_perimeter(b))
    if perim <= 0:
        return False
    return span <= perim * max_span_ratio
