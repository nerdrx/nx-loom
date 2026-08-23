"""The organic suggestion lane: proposals from the sculpt's own curvature.

SPEC §7 discipline under test: the lane only ever writes ghost polylines,
nothing runs unprompted, a surface with nothing confident to say produces
nothing, and accepted proposals become ordinary arcs through the same commit
machinery as a hand-drawn stroke.
"""

import bpy
import numpy as np

from nx_loom.core.suggest import (curvature_alignment, face_frames,
                                  singularities, smooth_field, suggest)
from nx_loom.ops.layout import get_graph, rebuild_object


def _cyl(R=0.4, n=24, rows=10, h=2.0):
    V, T = [], []
    for r in range(rows + 1):
        z = -h / 2 + h * r / rows
        for i in range(n):
            a = 2 * np.pi * i / n
            V.append([R * np.cos(a), R * np.sin(a), z])
    for r in range(rows):
        for i in range(n):
            j = (i + 1) % n
            a, b = r * n + i, r * n + j
            c, d = (r + 1) * n + i, (r + 1) * n + j
            T.append([a, b, c])
            T.append([b, d, c])
    return np.array(V, float), np.array(T, int)


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # -- the field itself: a cylinder wall is the honest litmus test
    V, T = _cyl()
    theta, frames, pairs = smooth_field(V, T)
    e1, e2, _n = frames
    dirs = np.cos(theta)[:, None] * e1 + np.sin(theta)[:, None] * e2
    al = np.abs(dirs @ np.array([0, 0, 1.0]))
    score = np.maximum(al, np.sqrt(np.clip(1 - al ** 2, 0, 1)))
    out.append(("the field on a cylinder runs along axis and rings",
                float(score.mean()) > 0.99, f"mean {score.mean():.3f}"))
    sing = singularities(T, theta, frames, pairs)
    out.append(("and invents no poles on an open wall", len(sing) == 0,
                str(len(sing))))

    # -- a flat sheet has nothing confident to say
    n = 6
    Vf = np.array([[i, j, 0.0] for i in range(n) for j in range(n)])
    Tf = []
    for i in range(n - 1):
        for j in range(n - 1):
            a = i * n + j
            Tf.append([a, a + 1, a + n])
            Tf.append([a + 1, a + n + 1, a + n])
    polys, _pts = suggest(Vf, np.array(Tf))
    out.append(("a featureless flat sheet yields no proposals",
                len(polys) == 0, f"{len(polys)}"))

    # -- Poincare-Hopf: a sphere's field carries 8 quarter-turns of index
    def ico():
        t = (1 + 5 ** 0.5) / 2
        Vl = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0),
              (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
              (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
        Vl = [np.array(v) / np.linalg.norm(v) for v in Vl]
        F = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
             (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
             (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
             (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
        cache = {}

        def mid(a, b):
            k = (min(a, b), max(a, b))
            if k not in cache:
                m = Vl[a] + Vl[b]
                cache[k] = len(Vl)
                Vl.append(m / np.linalg.norm(m))
            return cache[k]
        F2 = []
        for a, b, c in F:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            F2 += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        return np.array(Vl), np.array(F2)
    Vs, Ts = ico()
    th, fr, pr = smooth_field(Vs, Ts)
    si = singularities(Ts, th, fr, pr)
    out.append(("a sphere's field carries its topological debt",
                abs(sum(si.values())) == 8,
                f"index sum {sum(si.values())} over {len(si)} poles"))

    # -- operator flow on a real object
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.25
    st.relax_iters = 2
    st.symmetry_axis = "NONE"
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object

    res = bpy.ops.nxloom.suggest_layout()
    out.append(("suggesting finishes", "FINISHED" in res, str(res)))
    graph = get_graph(obj)
    stored = graph.settings.get("suggestions") or []
    out.append(("proposals are stored as ghosts, not geometry",
                len(stored) > 0 and len(graph.arcs) == 0,
                f"{len(stored)} ghosts, {len(graph.arcs)} arcs"))

    res = bpy.ops.nxloom.suggest_accept()
    out.append(("accepting finishes", "FINISHED" in res, str(res)))
    graph = get_graph(obj)
    out.append(("accepted proposals are ordinary authored arcs",
                len(graph.arcs) > 0
                and all(a.mirror_of is None for a in graph.arcs.values())
                and not (graph.settings.get("suggestions") or []),
                f"{len(graph.arcs)} arcs"))
    rebuild_object(obj, bpy.context)
    out.append(("and the layout still rebuilds without crashing",
                obj.data is not None, ""))

    # discard path
    bpy.ops.nxloom.suggest_layout()
    graph = get_graph(obj)
    if graph.settings.get("suggestions"):
        n_arcs = len(graph.arcs)
        bpy.ops.nxloom.suggest_clear()
        graph = get_graph(obj)
        out.append(("discarding removes ghosts and touches nothing",
                    not (graph.settings.get("suggestions") or [])
                    and len(graph.arcs) == n_arcs, ""))

    out += run_field_reports()
    return out


def run_field_reports():
    """Fixes from the first real-avatar contact."""
    from nx_loom.core.symmetry import unpaired_arcs
    out = []

    # a trace must never jump between disconnected shells (toes!) — spheres,
    # because their singularities guarantee traces actually spawn
    def ico(cx):
        t = (1 + 5 ** 0.5) / 2
        Vl = [np.array(v, float) for v in
              [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0),
               (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
               (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]]
        Vl = [v / np.linalg.norm(v) * 0.45 for v in Vl]
        F = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
             (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
             (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
             (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
        cache = {}

        def mid(a, b):
            k = (min(a, b), max(a, b))
            if k not in cache:
                m = Vl[a] + Vl[b]
                cache[k] = len(Vl)
                Vl.append(m / np.linalg.norm(m) * 0.45)
            return cache[k]
        F2 = []
        for a, b, c in F:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            F2 += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        V = np.array(Vl) + np.array([cx, 0.0, 0.0])
        return V, np.array(F2)

    def shell(cx):
        return ico(cx)
    Va, Ta = shell(-1.2)
    Vb, Tb = shell(1.2)
    V = np.vstack([Va, Vb])
    T = np.vstack([Ta, Tb + len(Va)])
    polys, _ = suggest(V, T, presmooth=False)
    hops = 0
    for poly in polys:
        side = np.sign(poly[:, 0])
        if len(set(side[np.abs(poly[:, 0]) > 0.2])) > 1:
            hops += 1
    out.append(("shells actually produce traces", len(polys) > 0,
                f"{len(polys)}"))
    out.append(("traces never jump between disconnected shells", hops == 0,
                f"{hops} of {len(polys)} traces hopped"))

    # with symmetry on, proposals are one-sided; accepting mirrors them
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.25
    st.relax_iters = 2
    st.symmetry_axis = "X"
    st.symmetry_tolerance = 0.02
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object
    res = bpy.ops.nxloom.suggest_layout()
    out.append(("symmetric suggesting finishes", "FINISHED" in res, str(res)))
    graph = get_graph(obj)
    stored = graph.settings.get("suggestions") or []
    one_sided = all(
        np.asarray(f, dtype=float).reshape(-1, 3)[:, 0].mean() > -0.05
        for f in stored)
    out.append(("proposals live on one side only",
                len(stored) > 0 and one_sided, f"{len(stored)} ghosts"))
    if stored:
        bpy.ops.nxloom.suggest_accept()
        graph = get_graph(obj)
        n_mir = sum(1 for a in graph.arcs.values()
                    if a.mirror_of is not None or a.twin is not None)
        loose = unpaired_arcs(graph, "X", st.symmetry_tolerance)
        out.append(("accepted proposals come out mirrored",
                    n_mir > 0, f"{n_mir} paired of {len(graph.arcs)}"))
        out.append(("with nothing left unpaired", len(loose) == 0,
                    str(len(loose))))
    return out
