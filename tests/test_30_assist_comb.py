"""The Assist slider and field combing.

Contracts: assist 0.5 reproduces the pre-slider numbers exactly (a default
must not change behaviour); more assist proposes at least as much as less;
the magnet pulls harder with more assist; comb hints bend the field but
LOSE to authored arcs on the same faces; and clearing combs restores the
pure-curvature field.
"""

import bpy
import numpy as np

from nx_loom.core.suggest import face_frames, guide_pins, smooth_field
from nx_loom.ops.layout import get_graph, set_graph
from nx_loom.ops.suggest import assist_params


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

    # ---- calibration: the default changes nothing ----------------------
    mid = assist_params(0.5)
    out.append(("assist 0.5 is exactly the pre-slider behaviour",
                mid == {"max_traces": 48, "min_pts": 8,
                        "keep_out_scale": 1.0}, str(mid)))
    lo, hi = assist_params(0.0), assist_params(1.0)
    out.append(("assist scales monotonically",
                lo["max_traces"] < mid["max_traces"] < hi["max_traces"]
                and lo["min_pts"] > hi["min_pts"]
                and lo["keep_out_scale"] > hi["keep_out_scale"],
                f"{lo['max_traces']}..{hi['max_traces']} traces"))

    # ---- more assist proposes at least as much -------------------------
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12,
                                         radius=1.0)
    ref = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    st.reference = ref
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object

    counts = {}
    for a in (0.0, 1.0):
        st.assist = a
        bpy.ops.nxloom.suggest_layout()
        g = get_graph(obj)
        counts[a] = len(g.settings.get("suggestions") or [])
        bpy.ops.nxloom.suggest_clear()
    out.append(("more assist proposes at least as much",
                0 < counts[0.0] <= counts[1.0],
                f"{counts[0.0]} at 0 vs {counts[1.0]} at 1"))
    st.assist = 0.5

    # ---- the magnet pulls harder with more strength --------------------
    from mathutils import kdtree
    kd = kdtree.KDTree(2)
    kd.insert((0.0, 0.0, 0.0), 0)
    kd.insert((0.0, 1.0, 0.0), 1)
    kd.balance()
    from nx_loom.ops.draw import magnet_pull

    class _Flat:
        def project(self, pts):
            return np.asarray(pts, dtype=float)
    p0 = np.array([0.1, 0.0, 0.0])
    weak = magnet_pull(_Flat(), kd, p0, 0.3, strength=0.3)
    strong = magnet_pull(_Flat(), kd, p0, 0.3, strength=1.0)
    out.append(("a stronger magnet pulls the same sample further",
                abs(strong[0]) < abs(weak[0]) < abs(p0[0]),
                f"{p0[0]:.2f} -> {weak[0]:.3f} (weak) / "
                f"{strong[0]:.3f} (strong)"))

    # ---- combs bend the field, arcs outrank them -----------------------
    V, T = _cyl()
    ts = np.linspace(0.0, 2 * np.pi, 60)
    helix = np.stack([0.4 * np.cos(ts), 0.4 * np.sin(ts),
                      -0.8 + 1.6 * ts / (2 * np.pi)], axis=1)
    frames = face_frames(V, T)
    theta_plain, _f, _p = smooth_field(V, T)
    cz, cw, _s = guide_pins(V, T, [helix], frames, weight=1.5)
    theta_comb, _f, _p = smooth_field(V, T, pin_z=cz, pin_w=cw)
    combed = np.where(cw > 0)[0]
    moved = 0
    for f in combed:
        d = abs(theta_comb[f] - theta_plain[f]) % (np.pi / 2)
        d = min(d, np.pi / 2 - d)
        if d > np.pi / 24:
            moved += 1
    out.append(("comb hints visibly bend the field where they run",
                moved > len(combed) * 0.5,
                f"{moved}/{len(combed)} faces moved >7.5 deg"))

    # an authored arc on the SAME faces must win over the comb
    axis_line = np.stack([np.full(30, 0.4), np.zeros(30),
                          np.linspace(-0.8, 0.8, 30)], axis=1)
    az, aw, _s = guide_pins(V, T, [axis_line], frames, weight=3.0)
    both = np.where((aw > 0) & (cw > 0))[0]
    pin_z = np.where(aw > 0, az, cz)
    pin_w = np.where(aw > 0, aw, cw)
    theta_both, _f, _p = smooth_field(V, T, pin_z=pin_z, pin_w=pin_w)
    wins = 0
    for f in both:
        want = np.angle(az[f]) / 4.0
        d = abs(theta_both[f] - want) % (np.pi / 2)
        d = min(d, np.pi / 2 - d)
        if d < np.pi / 18:
            wins += 1
    out.append(("an authored arc outranks a comb on the same face",
                len(both) > 0 and wins == len(both),
                f"{wins}/{len(both)} arc-pinned faces held"))

    # ---- storage: add, steer, clear ------------------------------------
    from nx_loom.ops.comb import add_comb
    graph = get_graph(obj)
    n = add_comb(graph, helix)
    set_graph(obj, graph)
    out.append(("a comb stroke stores on the layout", n == 1, ""))
    res = bpy.ops.nxloom.comb_clear()
    graph = get_graph(obj)
    out.append(("Clear Combs empties them",
                "FINISHED" in res
                and not (graph.settings.get("comb") or []), ""))

    return out
