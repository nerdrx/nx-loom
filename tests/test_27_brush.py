"""The result brush: falloff, relax, surface glue, and delta capture.

Contracts: falloff is 1 at the centre and 0 outside; a tweak moves the
centre most and leaves the rim's far side alone; relax reduces roughness;
every displaced vertex lands back ON the reference; and a captured brush
session survives a rebuild — the whole point.
"""

import bpy
import numpy as np

from nx_loom.core.brush import falloff, relax, tweak, vert_adjacency
from nx_loom.ops.layout import get_graph, rebuild_object


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # ---- core math -----------------------------------------------------
    w = falloff(np.array([0.0, 0.5, 1.0, 2.0]), 1.0)
    out.append(("falloff is 1 at the centre, 0 outside",
                abs(w[0] - 1.0) < 1e-9 and w[3] == 0.0
                and 0.0 < w[1] < 1.0, f"{np.round(w, 3)}"))

    grid = np.stack(np.meshgrid(np.linspace(-2, 2, 9),
                                np.linspace(-2, 2, 9)), axis=-1)
    verts = np.concatenate([grid.reshape(-1, 2),
                            np.zeros((81, 1))], axis=1)
    moved, hit = tweak(verts, [0.0, 0.0, 0.0], 1.0, [0.0, 0.0, 0.5])
    centre = int(np.argmin(np.linalg.norm(verts[:, :2], axis=1)))
    far = int(np.argmax(np.linalg.norm(verts[:, :2], axis=1)))
    out.append(("tweak moves the centre fully and the far rim not at all",
                abs(moved[centre, 2] - 0.5) < 1e-9
                and moved[far, 2] == 0.0 and not hit[far],
                f"centre dz {moved[centre, 2]:.3f}"))

    quads = []
    for j in range(8):
        for i in range(8):
            a = j * 9 + i
            quads.append((a, a + 1, a + 10, a + 9))
    nbrs = vert_adjacency(quads, 81)
    rough = verts.copy()
    rng_z = np.tile([0.1, -0.1], 41)[:81]
    rough[:, 2] = rng_z
    smoothed, _hit = relax(rough, nbrs, [0.0, 0.0, 0.0], 3.0)
    r0 = float(np.abs(np.diff(rough[:, 2])).mean())
    r1 = float(np.abs(np.diff(smoothed[:, 2])).mean())
    out.append(("relax reduces roughness under the brush",
                r1 < r0 * 0.8, f"{r0:.3f} -> {r1:.3f}"))

    # ---- end to end: stroke, glue, capture, rebuild --------------------
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=6,
                                         radius=1.0)
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.25
    st.relax_iters = 2
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.nxloom.layout_from_selection()
    obj = bpy.context.active_object
    graph = get_graph(obj)

    from nx_loom.ops.brush import stroke_step
    from nx_loom.ops.draw import _surface_of
    ref = _surface_of(graph, bpy.context)
    me = obj.data
    n = len(me.vertices)
    co = np.empty(n * 3)
    me.vertices.foreach_get("co", co)
    verts = co.reshape(-1, 3).copy()
    nbrs = vert_adjacency([tuple(p.vertices) for p in me.polygons], n)

    target = int(np.argmax(verts[:, 2]))          # the pole
    before = verts[target].copy()
    moved, hit = stroke_step(verts, nbrs, ref, verts[target], 0.35,
                             np.array([0.15, 0.0, 0.0]), "TWEAK")
    reproj = np.asarray(ref.project(moved[hit]), dtype=float)
    on_surface = float(np.linalg.norm(reproj - moved[hit], axis=1).max())
    out.append(("displaced vertices stay glued to the reference",
                on_surface < 1e-6, f"max off-surface {on_surface:.2e}"))
    out.append(("the stroke actually moved the target",
                float(np.linalg.norm(moved[target] - before)) > 0.05, ""))

    me.vertices.foreach_set("co", moved.reshape(-1))
    me.update()
    res = bpy.ops.nxloom.capture_edits()
    rebuild_object(obj, bpy.context)
    co2 = np.empty(n * 3)
    me.vertices.foreach_get("co", co2)
    after = co2.reshape(-1, 3)
    kept = float(np.linalg.norm(after[target] - moved[target]))
    far_v = int(np.argmin(verts[:, 2]))
    far_drift = float(np.linalg.norm(after[far_v] - verts[far_v]))
    out.append(("a captured brush session survives the rebuild",
                "FINISHED" in res and kept < 0.02,
                f"target drift {kept:.4f}"))
    out.append(("while untouched vertices stay put",
                far_drift < 1e-4, f"far drift {far_drift:.6f}"))

    return out
