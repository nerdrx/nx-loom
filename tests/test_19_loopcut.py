"""Loop cut, repeat ring, and the arc-type hotkeys."""

import bmesh
import bpy
import numpy as np

from nx_loom.core.loopcut import plan_loop
from nx_loom.core.surface import Surface
from nx_loom.ops.draw import commit_path, commit_ring, ring_from_plane
from nx_loom.ops.layout import get_graph, rebuild_object, set_graph


def _survey(o):
    bm = bmesh.new()
    bm.from_mesh(o.data)
    d = dict(F=len(bm.faces),
             nm=sum(1 for e in bm.edges if len(e.link_faces) > 2),
             nonquad=sum(1 for f in bm.faces if len(f.verts) != 4))
    bm.free()
    return d


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # -- loop cut committed on a traced grid: an open strip
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=3, y_subdivisions=3, size=2.0)
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.3
    st.relax_iters = 0
    st.reproject = False
    st.symmetry_axis = "NONE"
    st.size_mode = "EDGE"
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.nxloom.layout_from_selection()
    if bpy.context.active_object.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
    obj = bpy.context.active_object
    graph = get_graph(obj)
    src = bpy.data.objects.get(graph.reference)
    surf = Surface(src, bpy.context.evaluated_depsgraph_get())

    # pick a vertical boundary arc and plan a horizontal cut at 30%
    target = next(aid for aid, a in graph.arcs.items()
                  if abs(np.asarray(a.path)[:, 0].std()) < 1e-6
                  and np.allclose(np.asarray(a.path)[0][0], -1.0))
    click = np.asarray(graph.arcs[target].path, dtype=float)
    click = click[0] + (click[-1] - click[0]) * 0.3
    res = plan_loop(graph, target, click)
    out.append(("a loop is planned through the whole strip",
                res is not None and not res[1] and len(res[0]) > 10,
                "" if res is None else f"{len(res[0])} samples"))

    poly, _ = res
    arcs_before = len(graph.arcs)
    r = commit_path(graph, surf, poly, 0.05, 0.01)
    out.append(("committing it splits every side it passes",
                r is not None and len(graph.arcs) >= arcs_before + 5,
                f"{arcs_before} -> {len(graph.arcs)} arcs"))
    set_graph(obj, graph)
    rep = rebuild_object(obj, bpy.context)
    stt = _survey(obj)
    out.append(("the cut layout still builds clean quads",
                stt["nm"] == 0 and stt["nonquad"] == 0
                and not rep["unsatisfied_patches"], str(stt)))

    # -- closed loop cut around a drawn sphere band
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    src = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.3
    st.relax_iters = 4
    st.symmetry_axis = "NONE"
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object
    surf = Surface(src, bpy.context.evaluated_depsgraph_get())
    graph = get_graph(obj)
    from nx_loom.ops.draw import commit_halo

    def ray_at(p):
        p = np.asarray(p, dtype=float)
        return (p * 3.0, -p / np.linalg.norm(p))
    h1, _, _ = commit_halo(graph, surf, ray_at([0, 0, 1]),
                           ray_at(np.array([0.35, 0, 0.94]) / 1.0))
    commit_halo(graph, surf, ray_at([0, 0, 1]),
                ray_at(np.array([0.6, 0, 0.8]) / 1.0), bridge_to=h1)
    set_graph(obj, graph)
    rebuild_object(obj, bpy.context)
    graph = get_graph(obj)
    wall = next(a for a, arc in graph.arcs.items() if arc.rail == "straight")
    mid = np.asarray(graph.arcs[wall].path, dtype=float)
    mid = mid[len(mid) // 2]
    res = plan_loop(graph, wall, mid)
    out.append(("cutting across the halo band closes into a ring",
                res is not None and res[1], ""))
    if res is not None and res[1]:
        poly, _ = res
        poly = np.asarray(surf.project(poly), dtype=float)
        m = len(poly) // 2
        first = commit_path(graph, surf, poly[:m + 1], 0.05, 0.008)
        commit_path(graph, surf, np.vstack([poly[m:], poly[:1]]),
                    0.05, 0.008, start_node=first[2])
        set_graph(obj, graph)
        rep = rebuild_object(obj, bpy.context)
        stt = _survey(obj)
        out.append(("the closed cut builds clean between the halos",
                    stt["nm"] == 0 and stt["nonquad"] == 0 and stt["F"] > 0,
                    str(stt)))

    # -- repeat ring: two swiped rings, then extrapolate the third
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.3, depth=3.0)
    src = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.25
    st.relax_iters = 2
    st.symmetry_axis = "NONE"
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object
    surf = Surface(src, bpy.context.evaluated_depsgraph_get())
    graph = get_graph(obj)

    def swipe(z):
        return ((np.array([-0.4, -6.0, z]), np.array([0, 1.0, 0])),
                (np.array([0.4, -6.0, z]), np.array([0, 1.0, 0])))
    ra, _, _ = commit_ring(graph, surf, *swipe(-1.0))
    rb = commit_ring(graph, surf, *swipe(-0.6), bridge_to=ra)
    set_graph(obj, graph)
    obj["nx_loom_last_ring"] = [int(n) for n in rb[0]]
    obj["nx_loom_ring_hist"] = [
        [0.0, 0.0, -1.0, 0.0, 0.0, 1.0, 0.3, 0.0, -1.0],
        [0.0, 0.0, -0.6, 0.0, 0.0, 1.0, 0.3, 0.0, -0.6],
    ]
    rebuild_object(obj, bpy.context)
    res = bpy.ops.nxloom.repeat_ring()
    out.append(("repeat ring finishes", "FINISHED" in res, str(res)))
    graph = get_graph(obj)
    ring_z = [np.asarray(a.path)[:, 2].mean() for a in graph.arcs.values()
              if a.rail == "surface"]
    out.append(("the third ring lands at the extrapolated spacing",
                any(abs(z + 0.2) < 0.08 for z in ring_z),
                f"ring z values ~ {sorted(round(z, 2) for z in set(np.round(ring_z, 1)))}"))
    out.append(("and it bridged to the previous one",
                sum(1 for a in graph.arcs.values()
                    if a.rail == "straight") >= 8,
                f"{sum(1 for a in graph.arcs.values() if a.rail == 'straight')} walls"))

    # -- hotkeys
    bpy.ops.nxloom.set_arc_type_key(kind="crease")
    out.append(("number-key operator switches the arc type",
                bpy.context.scene.nx_loom.arc_type == "crease", ""))
    bpy.context.scene.nx_loom.arc_type = "flow"
    from nx_loom.ui.tools import NXLOOM_TOOL_draw
    keys = {k[1]["type"] for k in NXLOOM_TOOL_draw.bl_keymap
            if k[0] in ("nxloom.loop_cut", "nxloom.repeat_ring",
                        "nxloom.set_arc_type_key")}
    out.append(("C, R and 1-4 are bound on the tool",
                {"C", "R", "ONE", "TWO", "THREE", "FOUR"} <= keys, str(keys)))
    return out
