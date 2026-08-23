"""Loop cut: one click on an arc inserts a parallel loop through the whole
quad strip.

The gesture quad modelers live in — Blender's Ctrl+R — except the layout
version knows things a mesh knife cannot: which side of each patch is
*opposite*, where the strip ends (a pole, a hole, a boundary), and how to run
the new arc through a patch's own transfinite parameterisation so it lands
proportionally, not just straight.

Pure numpy (SPEC §10). The operator feeds the resulting polyline to
``commit_path``, whose crossing machinery already splits every shared side it
passes through — the walk plans the route, the existing commit builds it.
"""

from __future__ import annotations

import numpy as np

MAX_STEPS = 512


def side_polyline(graph, patch, i):
    """One side's chain of arcs as a single polyline, in walk order."""
    pts = None
    for aid, rev in patch.sides[i]:
        p = np.asarray(graph.arcs[aid].path, dtype=float)
        if rev:
            p = p[::-1]
        pts = p.copy() if pts is None else np.vstack([pts, p[1:]])
    return pts


def _cum(pts):
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(seg)])


def point_at(pts, f):
    """Arc-length interpolation at fraction f in [0, 1]."""
    cum = _cum(pts)
    total = cum[-1]
    if total <= 0:
        return pts[0].copy()
    d = np.clip(f, 0.0, 1.0) * total
    i = int(np.searchsorted(cum, d, side="right") - 1)
    i = min(max(i, 0), len(pts) - 2)
    seg = cum[i + 1] - cum[i]
    t = 0.0 if seg <= 0 else (d - cum[i]) / seg
    return pts[i] + (pts[i + 1] - pts[i]) * t


def frac_at(pts, point):
    """Arc-length fraction of the closest point on the polyline."""
    point = np.asarray(point, dtype=float)
    a = pts[:-1]
    ab = pts[1:] - a
    denom = np.einsum("ij,ij->i", ab, ab)
    denom[denom < 1e-20] = 1e-20
    t = np.clip(np.einsum("ij,ij->i", point - a, ab) / denom, 0.0, 1.0)
    proj = a + ab * t[:, None]
    d = np.linalg.norm(proj - point, axis=1)
    i = int(np.argmin(d))
    cum = _cum(pts)
    total = cum[-1]
    if total <= 0:
        return 0.0
    return float((cum[i] + t[i] * (cum[i + 1] - cum[i])) / total)


def iso_across(graph, patch, entry_side, f, samples=10):
    """The transfinite iso-curve at fraction ``f`` across a quad patch.

    Runs from fraction ``f`` on the entry side to fraction ``1 - f`` on the
    opposite side (each measured along its own walk direction — the two sides
    run antiparallel around the boundary). Blending against the other two
    sides is what makes the cut land *proportionally* in a curved or tapered
    patch instead of as a straight chord.
    """
    if len(patch.sides) != 4:
        return None
    s = [side_polyline(graph, patch, (entry_side + k) % 4) for k in range(4)]
    if any(x is None or len(x) < 2 for x in s):
        return None
    c0, c1 = s[0][0], s[0][-1]
    c2, c3 = s[2][0], s[2][-1]

    out = []
    for k in range(samples + 1):
        v = k / samples
        p = ((1 - v) * point_at(s[0], f)
             + v * point_at(s[2], 1 - f)
             + (1 - f) * point_at(s[3], 1 - v)
             + f * point_at(s[1], v)
             - ((1 - f) * (1 - v) * c0 + f * (1 - v) * c1
                + f * v * c2 + (1 - f) * v * c3))
        out.append(p)
    return np.asarray(out, dtype=float)


def _arcs_of_side(patch, i):
    return [aid for aid, _rev in patch.sides[i]]


def _patch_with_arcs(graph, arc_ids, exclude_pid):
    want = set(arc_ids)
    for pid, patch in graph.patches.items():
        if pid == exclude_pid:
            continue
        for i in range(len(patch.sides)):
            if want & set(_arcs_of_side(patch, i)):
                return pid, i
    return None, None


def _walk(graph, pid, entry_side, entry_point, samples):
    """March across quad patches from one entry until the strip ends."""
    pieces = []
    visited = set()
    for _ in range(MAX_STEPS):
        patch = graph.patches.get(pid)
        if patch is None or len(patch.sides) != 4 or patch.fill == "hole":
            return pieces, False, None
        key = (pid, entry_side)
        if key in visited:
            return pieces, True, None      # came back around: closed
        visited.add(key)

        entry_pts = side_polyline(graph, patch, entry_side)
        f = frac_at(entry_pts, entry_point)
        iso = iso_across(graph, patch, entry_side, f, samples)
        if iso is None:
            return pieces, False, None
        pieces.append(iso)

        exit_side = (entry_side + 2) % 4
        exit_point = iso[-1]
        nxt_pid, nxt_side = _patch_with_arcs(
            graph, _arcs_of_side(patch, exit_side), pid)
        if nxt_pid is None:
            return pieces, False, exit_point   # open end at a boundary
        pid, entry_side, entry_point = nxt_pid, nxt_side, exit_point
    return pieces, False, None


def plan_loop(graph, arc_id, point, samples=10):
    """The full loop polyline through the strip containing ``arc_id``.

    Walks both directions from the clicked arc — a shared arc belongs to two
    patches, and the loop spans the whole ring, not half of it. Returns
    (polyline, closed) or None when the arc bounds no quad patch. The strip
    honestly stops at poles, holes and boundaries, exactly where a loop cut
    should.
    """
    hosts = []
    for pid, patch in graph.patches.items():
        for i in range(len(patch.sides)):
            if arc_id in _arcs_of_side(patch, i):
                hosts.append((pid, i))
    hosts = [(pid, i) for pid, i in hosts
             if len(graph.patches[pid].sides) == 4]
    if not hosts:
        return None

    point = np.asarray(point, dtype=float)
    pid0, side0 = hosts[0]
    fwd, closed, _ = _walk(graph, pid0, side0, point, samples)
    if not fwd:
        return None
    if closed:
        poly = fwd[0]
        for piece in fwd[1:]:
            poly = np.vstack([poly, piece[1:]])
        return poly, True

    back = []
    if len(hosts) > 1:
        pid1, side1 = hosts[1]
        back, _, _ = _walk(graph, pid1, side1, point, samples)

    poly = None
    for piece in reversed(back):
        seg = piece[::-1]
        poly = seg if poly is None else np.vstack([poly, seg[1:]])
    for piece in fwd:
        poly = piece if poly is None else np.vstack([poly, piece[1:]])
    return poly, False
