"""Magnet drawing and the quality heatmap.

Contracts: feature curves find a real crease and run along it, decisively
featureless surfaces yield NOTHING (the magnet then has nothing to say),
the pull attracts but never overrides the hand, and quad quality scores
squares at 1, stretch and shear proportionally lower, garbage at the bottom.
"""

import bpy
import numpy as np

from nx_loom.core.features import feature_curves
from nx_loom.core.quality import quad_quality
from nx_loom.ops.layout import get_graph


def _tent(n=21, fold=0.4):
    """A sheet folded along x=0 — one honest crease."""
    xs = np.linspace(-1.0, 1.0, n)
    ys = np.linspace(-1.0, 1.0, n)
    V, T = [], []
    for y in ys:
        for x in xs:
            V.append([x, y, fold * (1.0 - abs(x))])
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            b, c, d = a + 1, a + n, a + n + 1
            T.append([a, b, d])
            T.append([a, d, c])
    return np.asarray(V, dtype=float), np.asarray(T, dtype=int)


def _flat(n=15):
    V, T = _tent(n=n, fold=0.0)
    return V, T


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # ---- quality metric ------------------------------------------------
    sq = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    q1 = quad_quality(sq, [(0, 1, 2, 3)])[0]
    out.append(("a unit square scores 1", abs(q1 - 1.0) < 1e-9, f"{q1:.3f}"))

    rect = sq * [3.0, 1.0, 1.0]
    q2 = quad_quality(rect, [(0, 1, 2, 3)])[0]
    out.append(("a 3:1 rectangle scores a third",
                abs(q2 - 1 / 3) < 1e-6, f"{q2:.3f}"))

    para = np.array([[0, 0, 0], [1, 0, 0], [1.7, 0.7, 0], [0.7, 0.7, 0]])
    q3 = quad_quality(para, [(0, 1, 2, 3)])[0]
    out.append(("a sheared parallelogram scores its shear",
                0.15 < q3 < 0.45 and q3 < q1, f"{q3:.3f}"))

    degen = np.array([[0, 0, 0], [0, 0, 0], [1, 1, 0], [0, 1, 0]])
    q4 = quad_quality(degen, [(0, 1, 2, 3)])[0]
    out.append(("a degenerate quad scores the bottom", q4 < 0.05, f"{q4:.3f}"))

    # ---- feature curves ------------------------------------------------
    V, T = _tent()
    curves = feature_curves(V, T)
    found = False
    for c in curves:
        c = np.asarray(c)
        on_fold = float(np.abs(c[:, 0]).max()) < 0.16
        long_enough = float(c[:, 1].max() - c[:, 1].min()) > 1.0
        if on_fold and long_enough:
            found = True
    out.append(("the tent's fold becomes one long feature curve",
                found and len(curves) >= 1,
                f"{len(curves)} curve(s)"))

    out.append(("a flat sheet offers the magnet nothing",
                feature_curves(*_flat()) == [], ""))

    # a sphere bends the same everywhere — no crease stands out
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12)
    sph = bpy.context.active_object
    sv = np.array([tuple(v.co) for v in sph.data.vertices])
    sph.data.calc_loop_triangles()
    stt = np.array([tuple(t.vertices) for t in sph.data.loop_triangles])
    out.append(("a sphere offers the magnet nothing either",
                feature_curves(sv, stt) == [], ""))

    # ---- the pull, end to end on a real surface ------------------------
    bpy.ops.wm.read_factory_settings(use_empty=True)
    V, T = _tent()
    me = bpy.data.meshes.new("tent")
    quads = []
    n = 21
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            quads.append((a, a + 1, a + n + 1, a + n))
    me.from_pydata([tuple(v) for v in V], [], quads)
    me.update()
    tent = bpy.data.objects.new("tent", me)
    bpy.context.collection.objects.link(tent)
    bpy.context.view_layer.objects.active = tent
    tent.select_set(True)

    st = bpy.context.scene.nx_loom
    st.reference = tent
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object
    graph = get_graph(obj)

    from nx_loom.ops.draw import _surface_of, magnet_index, magnet_pull
    surface = _surface_of(graph, bpy.context)
    idx = magnet_index(surface, graph, bpy.context)
    out.append(("the magnet index finds the tent's fold",
                idx["kd"] is not None and len(idx["curves"] or []) >= 1,
                f"{len(idx['curves'] or [])} curve(s)"))

    p_near = np.array([0.12, 0.1, 0.4 * (1 - 0.12)])
    pulled = magnet_pull(surface, idx["kd"], p_near, radius=0.3)
    out.append(("a nearby sample is pulled toward the crease",
                abs(float(pulled[0])) < abs(float(p_near[0])) * 0.8,
                f"x {p_near[0]:.3f} -> {pulled[0]:.3f}"))

    p_far = np.array([0.8, 0.0, 0.4 * (1 - 0.8)])
    kept = magnet_pull(surface, idx["kd"], p_far, radius=0.05)
    out.append(("a sample outside the radius is left alone",
                np.allclose(kept, p_far, atol=1e-6), ""))

    from nx_loom.ui import overlay
    st.magnet = True
    armed = overlay._magnet["curves"]
    st.magnet = False
    disarmed = overlay._magnet["curves"]
    out.append(("the panel toggle arms and disarms the guide lines",
                bool(armed) and disarmed is None,
                f"{len(armed or [])} shown"))

    return out
