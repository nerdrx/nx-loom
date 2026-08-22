"""UVs straight out of the layout.

An unwrapper has to infer a parameterisation from a triangle soup and then
relax it to spread the distortion around. There is nothing to infer here: a
quad patch **is** a `p x q` grid, so it unwraps to a `p x q` rectangle exactly.

Neighbouring patches are merged into one island by propagating a rigid lattice
transform across the arc they share — which only works because the quantiser
already guarantees both sides of that arc carry the same number of segments.
Merging stops at an arc typed `seam`, and at any placement that would overlap
what is already laid down: a surface that closes on itself has to be cut
somewhere, and the cut is placed where the walk runs into itself.
"""

from __future__ import annotations

import numpy as np

# rotations by 90 degrees, counter-clockwise
_ROT = (
    lambda x, y: (x, y),
    lambda x, y: (-y, x),
    lambda x, y: (-x, -y),
    lambda x, y: (y, -x),
)
_DIR = ((1, 0), (0, 1), (-1, 0), (0, -1))


def _apply(rot, off, x, y):
    rx, ry = _ROT[rot](x, y)
    return rx + off[0], ry + off[1]


def _corners(p, q):
    return ((0, 0), (p, 0), (p, q), (0, q))


def _side_point(p, q, side, t):
    c = _corners(p, q)[side]
    d = _DIR[side]
    return c[0] + d[0] * t, c[1] + d[1] * t


def _rot_between(src, dst):
    """Rotation index taking axis-aligned vector src onto dst, or None."""
    for r in range(4):
        if _ROT[r](*src) == tuple(dst):
            return r
    return None


def patch_dims(graph, patch, counts):
    sides = [sum(counts[a] for a in side) for side in patch.arc_sides()]
    if len(sides) != 4:
        return None
    return sides[0], sides[1]


def _side_offsets(patch, counts):
    """For each side, the cumulative offset of every arc along it."""
    out = []
    for side in patch.sides:
        acc, entries = 0, []
        for aid, _rev in side:
            entries.append((aid, acc, counts[aid]))
            acc += counts[aid]
        out.append(entries)
    return out


def build_islands(graph, counts, quad_patches):
    """Group quad patches into islands. -> {pid: (island, rot, offset)}."""
    dims, offsets = {}, {}
    for pid in quad_patches:
        patch = graph.patches[pid]
        d = patch_dims(graph, patch, counts)
        if d is None:
            continue
        dims[pid] = d
        offsets[pid] = _side_offsets(patch, counts)

    # arc -> the patches that use it, with side index and offset along the side
    users = {}
    for pid, sides in offsets.items():
        for si, entries in enumerate(sides):
            for aid, off, n in entries:
                users.setdefault(aid, []).append((pid, si, off, n))

    placement, island_of = {}, {}
    island = 0
    for seed in sorted(dims):
        if seed in placement:
            continue
        placement[seed] = (0, (0, 0))
        island_of[seed] = island
        cells = set()
        _occupy(cells, dims[seed], placement[seed])
        queue = [seed]
        while queue:
            cur = queue.pop(0)
            for si, entries in enumerate(offsets[cur]):
                for aid, off, n in entries:
                    arc = graph.arcs.get(aid)
                    if arc is None or arc.type == "seam":
                        continue
                    for (pid, sj, off_b, n_b) in users.get(aid, ()):
                        if pid == cur or pid in placement or pid not in dims:
                            continue
                        if n_b != n:
                            continue
                        t = _transform(dims[cur], placement[cur], si, off, n,
                                       dims[pid], sj, off_b)
                        if t is None:
                            continue
                        if _collides(cells, dims[pid], t):
                            continue
                        placement[pid] = t
                        island_of[pid] = island
                        _occupy(cells, dims[pid], t)
                        queue.append(pid)
        island += 1

    return {pid: (island_of[pid],) + placement[pid] for pid in placement}


def _transform(dim_a, place_a, side_a, off_a, n, dim_b, side_b, off_b):
    """Place B so its shared arc lands on A's, traversed the other way."""
    rot_a, off_world = place_a
    pa, qa = dim_a
    pb, qb = dim_b

    a0 = _apply(rot_a, off_world, *_side_point(pa, qa, side_a, off_a))
    a1 = _apply(rot_a, off_world, *_side_point(pa, qa, side_a, off_a + n))

    b0 = _side_point(pb, qb, side_b, off_b)
    b1 = _side_point(pb, qb, side_b, off_b + n)

    want = (a0[0] - a1[0], a0[1] - a1[1])
    have = (b1[0] - b0[0], b1[1] - b0[1])
    rot = _rot_between(have, want)
    if rot is None:
        return None
    rb1 = _ROT[rot](*b1)
    return rot, (a0[0] - rb1[0], a0[1] - rb1[1])


def _cells(dim, place):
    p, q = dim
    rot, off = place
    for i in range(p):
        for j in range(q):
            pts = [_apply(rot, off, i + dx, j + dy)
                   for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))]
            yield (min(x for x, _ in pts), min(y for _, y in pts))


def _occupy(cells, dim, place):
    cells.update(_cells(dim, place))


def _collides(cells, dim, place):
    return any(c in cells for c in _cells(dim, place))


def layout_uvs(graph, counts, report, verts=None, quads=None, margin=0.01):
    """Per-quad corner UVs for the whole mesh. -> list of 4 (u, v) per quad.

    Islands are scaled so texel density is even — a patch covering more surface
    gets proportionally more UV space — then shelf-packed into the unit square.
    """
    quad_patch = report.get("quad_patch") or []
    lattice = report.get("quad_lattice") or []
    charts = report.get("charts") or {}
    if not quad_patch or len(quad_patch) != len(lattice):
        return None

    quad_pids = [pid for pid in set(quad_patch)
                 if len(graph.patches[pid].sides) == 4]
    placement = build_islands(graph, counts, quad_pids)

    # every non-quad patch, and any quad the walk could not place, is its own
    # island keyed per sub-block so nothing is ever left without UVs
    groups = {}
    coords = [None] * len(quad_patch)
    for k, pid in enumerate(quad_patch):
        corners = lattice[k]
        if pid in placement:
            island, rot, off = placement[pid]
            key = ("q", island)
            pts = [_apply(rot, off, x, y) for x, y in corners]
        else:
            chart = charts.get(pid) or {}
            blk = chart.get("block_of")
            b = blk[k - _first_index(quad_patch, pid)] if blk else 0
            key = ("n", pid, b)
            pts = [(x, y) for x, y in corners]
        coords[k] = pts
        groups.setdefault(key, []).append(k)

    # scale each island for even texel density, then pack
    boxes = []
    for key, ks in groups.items():
        pts = [p for k in ks for p in coords[k]]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        cells = len(ks)
        # True 3D area of exactly these faces. Deriving it from the patch
        # instead was wrong for n-sided patches, whose sub-blocks each got
        # credited with the whole patch's area and came out ~n times too big.
        area = _face_area(verts, quads, ks) if verts is not None else \
            _island_area(graph, quad_patch, ks, report)
        scale = (area / max(cells, 1)) ** 0.5 if cells else 1.0
        boxes.append([key, min(xs), min(ys), max(w, 1e-9) * scale,
                      max(h, 1e-9) * scale, scale])

    placed = _shelf_pack(boxes, margin)
    out = [None] * len(quad_patch)
    for key, ks in groups.items():
        ox, oy, scale, minx, miny = placed[key]
        for k in ks:
            out[k] = [((x - minx) * scale + ox, (y - miny) * scale + oy)
                      for x, y in coords[k]]
    info = {
        "islands": len(groups),
        "merged_patches": len(placement),
        "loose_patches": len(set(quad_patch)) - len(placement),
    }
    return out, info


def _first_index(seq, value):
    for i, v in enumerate(seq):
        if v == value:
            return i
    return 0


def _face_area(verts, quads, ks):
    total = 0.0
    for k in ks:
        q = quads[k]
        a, b, c, d = (np.asarray(verts[i], dtype=float) for i in q)
        total += 0.5 * float(np.linalg.norm(np.cross(c - a, d - b)))
    return total or 1.0


def _island_area(graph, quad_patch, ks, report):
    pids = {quad_patch[k] for k in ks}
    return sum(graph.patch_area(p) for p in pids) or 1.0


def _shelf_pack(boxes, margin):
    """Shelf packing into the unit square, tallest first."""
    boxes = sorted(boxes, key=lambda b: -b[4])
    total = sum(b[3] * b[4] for b in boxes) or 1.0
    width = (total ** 0.5) * 1.35

    out, x, y, shelf, max_x = {}, 0.0, 0.0, 0.0, 0.0
    for key, minx, miny, w, h, scale in boxes:
        if x + w > width and x > 0:
            x, y = 0.0, y + shelf + margin
            shelf = 0.0
        out[key] = [x, y, scale, minx, miny]
        x += w + margin
        max_x = max(max_x, x)
        shelf = max(shelf, h)
    height = y + shelf

    # normalise by what was actually used: a single island wider than the
    # nominal shelf width would otherwise run past 1.0
    extent = max(max_x, height, 1e-9)
    for key in out:
        out[key][0] /= extent
        out[key][1] /= extent
        out[key][2] /= extent
    return out
