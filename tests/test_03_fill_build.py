"""Fill and the rebuild pipeline: welded by construction, no orphans."""

import numpy as np

from nx_loom.core import fill as F
from nx_loom.core.build import build, mesh_stats
from nx_loom.core.graph import from_edge_chains, trace_chains
from nx_loom.core.surface import polyline_length, resample


def _ngon(counts, radius=1.0):
    n = len(counts)
    corners = [np.array([np.cos(2 * np.pi * i / n), np.sin(2 * np.pi * i / n), 0.0]) * radius
               for i in range(n)]
    sides = []
    for i in range(n):
        a, b = corners[i], corners[(i + 1) % n]
        m = counts[i]
        sides.append([a + (b - a) * (k / m) for k in range(m + 1)])
    return sides


def _check(verts, quads):
    cnt = {}
    for q in quads:
        for k in range(4):
            e = tuple(sorted((q[k], q[(k + 1) % 4])))
            cnt[e] = cnt.get(e, 0) + 1
    used = {i for q in quads for i in q}
    return {
        "orphans": len(verts) - len(used),
        "nonmanifold": sum(1 for c in cnt.values() if c > 2),
        "boundary": sum(1 for c in cnt.values() if c == 1),
        "euler": len(verts) - len(cnt) + len(quads),
    }


def _grid_graph(n):
    idx = lambda i, j: i * n + j
    pts = [[float(i), float(j), 0.0] for i in range(n) for j in range(n)]
    edges = []
    for i in range(n):
        for j in range(n):
            if i < n - 1:
                edges.append((idx(i, j), idx(i + 1, j)))
            if j < n - 1:
                edges.append((idx(i, j), idx(i, j + 1)))
    g = from_edge_chains(pts, trace_chains(edges, pts))
    g.discover_patches(normal_at=lambda p: [0, 0, 1])
    return g


def run():
    out = []

    # resampling preserves endpoints exactly — they are shared corners
    path = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [2, 1, 0]]
    r = resample(path, 6)
    seg = np.linalg.norm(np.diff(r, axis=0), axis=1)
    out.append(("resample is even", float(seg.std()) < 1e-9, f"std={seg.std():.2e}"))
    out.append(("resample keeps endpoints",
                np.allclose(r[0], path[0]) and np.allclose(r[-1], path[-1]), ""))
    out.append(("resample preserves length",
                abs(polyline_length(r) - 3.0) < 1e-9, ""))

    # fills of every supported patch arity
    for counts in [(3, 4, 3, 4), (2, 2, 2), (3, 4, 3), (2, 2, 2, 2, 2), (4, 3, 3, 4, 4)]:
        res = F.fill_patch(_ngon(counts), relax_iters=5)
        if res is None:
            out.append((f"fill {counts}", False, "returned None"))
            continue
        v, q, *_ = res
        st = _check(v, q)
        ok = (st["orphans"] == 0 and st["nonmanifold"] == 0
              and st["euler"] == 1 and st["boundary"] == sum(counts))
        out.append((f"fill {counts}", ok, str(st)))

    # a genuinely unfillable patch is refused, not fudged
    out.append(("odd-total triangle refused",
                F.fill_patch(_ngon((3, 3, 3)), relax_iters=0) is None, ""))

    # the rebuild pipeline: patches weld, no distance merge involved
    for n, te in ((3, 0.5), (3, 0.2), (5, 0.3), (5, 0.15)):
        g = _grid_graph(n)
        v, q, _, rep = build(g, target_edge=te, relax_iters=8)
        st = mesh_stats(v, q)
        ok = (st["nonmanifold_edges"] == 0 and st["euler"] == 1
              and rep["dropped_verts"] == 0 and not rep["failed_patches"])
        out.append((f"build {n-1}x{n-1} layout @ {te}", ok,
                    f"{st['quads']} quads, euler={st['euler']}, "
                    f"nm={st['nonmanifold_edges']}, dropped={rep['dropped_verts']}"))

    # welding is exact: a k-subdivided 2x2 layout must be a perfect (2k+1)^2 grid
    g = _grid_graph(3)
    v, q, _, rep = build(g, target_edge=1.0 / 9.0, relax_iters=0)
    side = int(round(len(v) ** 0.5))
    out.append(("no duplicate boundary verts",
                side * side == len(v) and len(q) == (side - 1) ** 2,
                f"{len(v)} verts, {len(q)} quads"))

    # density monotonicity: finer target never yields fewer quads
    prev = -1
    mono = True
    for te in (1.0, 0.5, 0.25, 0.125, 0.0625):
        g = _grid_graph(3)
        _, q, _, _ = build(g, target_edge=te, relax_iters=0)
        if len(q) < prev:
            mono = False
        prev = len(q)
    out.append(("density is monotone", mono, ""))
    return out
