"""Map baking: a bumpy sculpt's detail lands in the normal map.

Contract: baking onto a coarse layout of a bumpy reference produces a
tangent-space normal map that is neutral on average but actually carries
the bumps (nonzero variance), the image is packed, UVs are auto-generated
when missing, and the operator restores the scene state it touched.
"""

import bpy
import numpy as np

from nx_loom.ops.layout import get_graph


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24,
                                         radius=1.0)
    ref = bpy.context.active_object
    me = ref.data
    n = len(me.vertices)
    co = np.empty(n * 3)
    me.vertices.foreach_get("co", co)
    v = co.reshape(-1, 3)
    bump = 1.0 + 0.04 * (np.sin(6 * v[:, 0]) * np.sin(6 * v[:, 1])
                         * np.sin(6 * v[:, 2]))
    me.vertices.foreach_set("co", (v * bump[:, None]).reshape(-1))
    me.update()

    # the low mesh comes from a SMOOTH sphere; the bumpy one becomes the
    # bake reference afterwards. Safe here only because nothing rebuilds
    # after the swap — pins still belong to the smooth surface.
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8,
                                         radius=1.0)
    smooth = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.18
    st.relax_iters = 2
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.nxloom.layout_from_selection()
    obj = bpy.context.active_object
    graph = get_graph(obj)
    if graph is None or not len(obj.data.polygons):
        out.append(("bake fixture builds", False, "no generated mesh"))
        return out
    graph.reference = ref.name
    from nx_loom.ops.layout import set_graph
    set_graph(obj, graph)
    smooth.hide_set(True)

    had_uvs = "NXLoom" in obj.data.uv_layers
    n_mats_before = len(obj.data.materials)
    engine_before = bpy.context.scene.render.engine

    res = bpy.ops.nxloom.bake_maps(resolution="128")
    out.append(("the bake finishes", "FINISHED" in res, str(res)))

    img = bpy.data.images.get(f"{obj.name}_normal")
    out.append(("a normal map exists at the asked size and is packed",
                img is not None and tuple(img.size) == (128, 128)
                and img.packed_file is not None,
                "" if img else "missing"))

    if img is not None:
        px = np.array(img.pixels[:]).reshape(-1, 4)
        mean = px[:, :3].mean(axis=0)
        std = float(px[:, 0].std())
        neutralish = abs(mean[0] - 0.5) < 0.1 and mean[2] > 0.7
        out.append(("the map is neutral on average but carries the bumps",
                    neutralish and std > 0.01,
                    f"mean {np.round(mean, 2)}, std {std:.3f}"))

    out.append(("UVs were generated on the way",
                not had_uvs and "NXLoom" in obj.data.uv_layers, ""))
    out.append(("the operator restores engine and materials",
                bpy.context.scene.render.engine == engine_before
                and len(obj.data.materials) == n_mats_before,
                bpy.context.scene.render.engine))

    return out
