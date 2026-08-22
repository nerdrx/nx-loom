"""Patch fill: boundary polylines in, quads out.

Pure numpy, no bpy (SPEC §10). Boundary vertices are *inputs* — they are owned
by the arcs and shared between neighbouring patches, so patches are welded by
construction and never by a distance merge (SPEC §3).
"""

from __future__ import annotations

import numpy as np

from .quantize import solve_splits


def coons(sides):
    """Discrete Coons patch from four boundary polylines.

    ``sides`` are CCW: side0 c0->c1, side1 c1->c2, side2 c2->c3, side3 c3->c0.
    len(side0) == len(side2) == p+1 and len(side1) == len(side3) == q+1.
    Returns a (p+1, q+1, 3) grid whose borders are exactly the inputs.
    """
    s0, s1, s2, s3 = (np.asarray(s, dtype=float) for s in sides)
    p, q = len(s0) - 1, len(s1) - 1
    if len(s2) != p + 1 or len(s3) != q + 1:
        raise ValueError(f"coons: side lengths {[len(s) for s in sides]} are not a quad")

    grid = np.zeros((p + 1, q + 1, 3))
    bottom = s0                 # j = 0,  i ascending
    right = s1                  # i = p,  j ascending
    top = s2[::-1]              # j = q,  i ascending
    left = s3[::-1]             # i = 0,  j ascending

    u = np.linspace(0.0, 1.0, p + 1)[:, None]
    v = np.linspace(0.0, 1.0, q + 1)[:, None]

    for j in range(q + 1):
        vv = v[j, 0]
        ruled_v = (1 - u) * left[j] + u * right[j]
        ruled_u = (1 - vv) * bottom + vv * top
        bilinear = (
            (1 - u) * (1 - vv) * bottom[0]
            + u * (1 - vv) * bottom[p]
            + (1 - u) * vv * top[0]
            + u * vv * top[p]
        )
        grid[:, j, :] = ruled_v + ruled_u - bilinear

    # borders are exact by construction, but pin them against float drift
    grid[:, 0, :] = bottom
    grid[:, q, :] = top
    grid[0, :, :] = left
    grid[p, :, :] = right
    return grid


def grid_quads(p, q, base=0):
    """Index quads for a (p+1) x (q+1) grid flattened row-major over i."""
    out = []
    for i in range(p):
        for j in range(q):
            out.append((
                base + i * (q + 1) + j,
                base + (i + 1) * (q + 1) + j,
                base + (i + 1) * (q + 1) + j + 1,
                base + i * (q + 1) + j + 1,
            ))
    return out


def relax(verts, quads, fixed, iters=20, damping=0.5, project=None):
    """Laplacian relaxation of interior vertices, optionally reprojected.

    ``fixed`` is a boolean mask over ``verts``; ``project`` maps an (n,3) array
    of positions onto the reference surface and returns the same shape.
    """
    verts = np.asarray(verts, dtype=float).copy()
    if not len(quads):
        return verts
    nbr = [set() for _ in range(len(verts))]
    for a, b, c, d in quads:
        for x, y in ((a, b), (b, c), (c, d), (d, a)):
            nbr[x].add(y)
            nbr[y].add(x)
    movable = [i for i in range(len(verts)) if not fixed[i] and nbr[i]]
    if not movable:
        return verts
    for _ in range(iters):
        upd = verts.copy()
        for i in movable:
            mean = verts[list(nbr[i])].mean(axis=0)
            upd[i] = verts[i] + damping * (mean - verts[i])
        verts = upd
        if project is not None and movable:
            verts[movable] = project(verts[movable])
    return verts


def fill_quad_patch(sides):
    """4-sided patch. Returns (verts (N,3), quads, boundary_slots, fixed).

    ``boundary_slots`` maps ("side", side_index, position) -> vertex index, so
    the caller can splice in the arc-owned vertices it already instantiated.
    Returns None when opposite sides disagree — that is a quantizer result the
    caller must report, not an exception that takes the whole rebuild down.
    """
    if len(sides) != 4 or len(sides[0]) != len(sides[2]) or len(sides[1]) != len(sides[3]):
        return None
    grid = coons(sides)
    p, q = grid.shape[0] - 1, grid.shape[1] - 1
    verts = grid.reshape(-1, 3)
    quads = grid_quads(p, q)

    slots = {}
    for i in range(p + 1):
        slots[("side", 0, i)] = i * (q + 1) + 0
        slots[("side", 2, p - i)] = i * (q + 1) + q
    for j in range(q + 1):
        slots[("side", 1, j)] = p * (q + 1) + j
        slots[("side", 3, q - j)] = 0 * (q + 1) + j

    fixed = np.zeros(len(verts), dtype=bool)
    for v in slots.values():
        fixed[v] = True
    return verts, quads, slots, fixed


def fill_ngon_patch(sides):
    """n-sided patch, n != 4. One interior vertex, n tensor sub-grids.

    Returns (verts, quads, boundary_slots, fixed) or None when the side counts
    admit no valid integer split (SPEC §2) — the caller reports it.
    """
    sides = [np.asarray(s, dtype=float) for s in sides]
    n = len(sides)
    counts = [len(s) - 1 for s in sides]
    a = solve_splits(counts)
    if a is None:
        return None

    verts = []

    def add(pt):
        verts.append(np.asarray(pt, dtype=float))
        return len(verts) - 1

    # 1. side vertices; the last point of side i IS the first of side i+1
    side_idx = [[None] * (counts[i] + 1) for i in range(n)]
    for i in range(n):
        for k in range(counts[i]):
            side_idx[i][k] = add(sides[i][k])
    for i in range(n):
        side_idx[i][counts[i]] = side_idx[(i + 1) % n][0]

    # 2. centre and the spokes centre -> split point on side i
    corners = np.vstack([sides[i][0] for i in range(n)])
    splits = np.vstack([sides[i][a[i]] for i in range(n)])
    center_idx = add(np.vstack([corners, splits]).mean(axis=0))
    center = verts[center_idx]

    spoke_idx = []
    for i in range(n):
        m = counts[(i - 1) % n] - a[(i - 1) % n]      # == a[(i + 1) % n]
        ids = [center_idx]
        for k in range(1, m):
            ids.append(add(center + (splits[i] - center) * (k / m)))
        ids.append(side_idx[i][a[i]])                 # tip is the split vertex
        spoke_idx.append(ids)

    # 3. one tensor block per corner: [split_{i-1} .. corner_i .. split_i] x spokes
    quads = []
    for i in range(n):
        prev = (i - 1) % n
        row_b = [side_idx[i][k] for k in range(0, a[i] + 1)]              # corner_i -> split_i
        row_a = [side_idx[prev][k] for k in range(a[prev], counts[prev] + 1)]  # split_{i-1} -> corner_i
        sa = spoke_idx[prev]                                              # centre -> split_{i-1}
        sb = spoke_idx[i]                                                 # centre -> split_i
        nx_, ny_ = len(row_b) - 1, len(row_a) - 1
        if nx_ != len(sa) - 1 or ny_ != len(sb) - 1:
            return None

        block = [[None] * (ny_ + 1) for _ in range(nx_ + 1)]
        for x in range(nx_ + 1):
            block[x][0] = row_b[x]                    # y=0 edge: corner_i -> split_i
            block[x][ny_] = sa[nx_ - x]               # y=ny edge: split_{i-1} -> centre
        for y in range(ny_ + 1):
            block[0][y] = row_a[ny_ - y]              # x=0 edge: corner_i -> split_{i-1}
            block[nx_][y] = sb[ny_ - y]               # x=nx edge: split_i -> centre

        for x in range(1, nx_):
            for y in range(1, ny_):
                fu, fv = x / nx_, y / ny_
                pt = (
                    (1 - fu) * verts[block[0][y]] + fu * verts[block[nx_][y]]
                    + (1 - fv) * verts[block[x][0]] + fv * verts[block[x][ny_]]
                    - ((1 - fu) * (1 - fv) * verts[block[0][0]]
                       + fu * (1 - fv) * verts[block[nx_][0]]
                       + (1 - fu) * fv * verts[block[0][ny_]]
                       + fu * fv * verts[block[nx_][ny_]])
                )
                block[x][y] = add(pt)

        for x in range(nx_):
            for y in range(ny_):
                quads.append((block[x][y], block[x + 1][y],
                              block[x + 1][y + 1], block[x][y + 1]))

    slots = {}
    for i in range(n):
        for k in range(counts[i] + 1):
            slots[("side", i, k)] = side_idx[i][k]

    verts = np.vstack(verts)
    fixed = np.zeros(len(verts), dtype=bool)
    for v in slots.values():
        fixed[v] = True
    return verts, quads, slots, fixed


def fill_patch(sides, relax_iters=20, project=None):
    """Dispatch on side count. Returns (verts, quads, slots) or None."""
    n = len(sides)
    if n == 4:
        res = fill_quad_patch(sides)
    elif n >= 3:
        res = fill_ngon_patch(sides)
    else:
        return None
    if res is None:
        return None
    verts, quads, slots, fixed = res
    if relax_iters:
        verts = relax(verts, quads, fixed, iters=relax_iters, project=project)
    return verts, quads, slots
