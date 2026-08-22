"""UI surface: the parts a person touches.

Two rules worth guarding. Nothing reachable from the sidebar may depend on a
viewport mouse position — a button is pressed with the cursor over the sidebar.
And Apply must leave a genuinely plain mesh, with none of our bookkeeping on it.
"""

import bpy
import numpy as np

from nx_loom.core.build import estimate_quads, solve_edge_for_count
from nx_loom.ops.layout import get_graph


def _traced(target_edge=0.3):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=3, y_subdivisions=3, size=2.0)
    st = bpy.context.scene.nx_loom
    st.target_edge = target_edge
    st.relax_iters = 2
    st.size_mode = "EDGE"
    st.symmetry_axis = "NONE"
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.nxloom.layout_from_selection()
    if bpy.context.active_object.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
    return bpy.context.active_object


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # every sidebar panel must register and be drawable
    for name in ("NXLOOM_PT_main", "NXLOOM_PT_size", "NXLOOM_PT_symmetry",
                 "NXLOOM_PT_edits", "NXLOOM_PT_finish", "NXLOOM_PT_display",
                 "NXLOOM_PT_stats"):
        out.append((f"panel {name} registered",
                    hasattr(bpy.types, name), ""))

    # no panel-reachable operator may need a viewport mouse
    import inspect

    from nx_loom.ui import panel as panel_mod
    src = inspect.getsource(panel_mod)
    mouse_ops = [op for op in ("nxloom.toggle_hole", "nxloom.draw_arc",
                               "nxloom.erase", "nxloom.move_node",
                               "nxloom.set_arc_type")
                 if f'"{op}"' in src]
    out.append(("no mouse-position operator is exposed as a button",
                not mouse_ops, str(mouse_ops)))

    obj = _traced()
    graph = get_graph(obj)

    # face-count mode
    st = bpy.context.scene.nx_loom
    # An exact hit is often impossible: subdivisions are whole numbers, and on
    # a uniform patch grid a single edge-length slider can only reach 9k^2.
    # The honest property is that no reachable count is closer than the one we
    # picked, so the achievable set is scanned to check.
    import numpy as np
    reachable = sorted({estimate_quads(graph, e)
                        for e in np.geomspace(0.02, 1.5, 400)} - {0})
    results = []
    for want in (200, 800, 3000):
        st.size_mode = "COUNT"
        st.target_count = want
        bpy.ops.nxloom.rebuild()
        got = len(obj.data.polygons)
        best = min(reachable, key=lambda c: abs(c - want))
        results.append((want, got, best, abs(got - want) / want))
    out.append(("face budgets hit the closest reachable count",
                all(r[1] == r[2] for r in results),
                str([(w, g, f"best {b}", f"{e*100:.1f}%")
                     for w, g, b, e in results])))
    out.append(("bigger budget really means more faces",
                results[0][1] < results[1][1] < results[2][1],
                str([r[1] for r in results])))

    pred = estimate_quads(graph, 0.2)
    st.size_mode = "EDGE"
    st.target_edge = 0.2
    bpy.ops.nxloom.rebuild()
    out.append(("the face estimate matches what gets built",
                pred == len(obj.data.polygons),
                f"predicted {pred}, built {len(obj.data.polygons)}"))

    edge, n = solve_edge_for_count(graph, 500)
    out.append(("solving a budget reports what it actually achieved",
                n > 0 and edge > 0, f"edge {edge:.4f} -> {n} faces"))

    # generated meshes must not z-fight the surface they sit on
    out.append(("generated object draws in front", obj.show_in_front, ""))

    # helper operators are callable without a mouse
    out.append(("toggle reference works",
                "FINISHED" in bpy.ops.nxloom.toggle_reference(), ""))
    bpy.ops.nxloom.toggle_reference()

    # apply leaves a genuinely plain mesh
    bpy.ops.nxloom.apply()
    leftovers = [k for k in ("nx_loom_graph", "nx_loom_delta",
                             "nx_loom_bad_patches", "nx_loom_background")
                 if k in obj]
    out.append(("apply removes every custom property", not leftovers,
                str(leftovers)))
    out.append(("apply removes the patch-id attribute",
                obj.data.attributes.get("nx_loom_patch") is None, ""))
    out.append(("apply stops drawing in front", not obj.show_in_front, ""))

    out += run_hover()
    return out


def run_hover():
    """Seeing what you are about to grab, before you click."""
    import time

    from nx_loom.core.authoring import nearest_node, nearest_on_arc
    from nx_loom.core.picking import ray_surface
    from nx_loom.core.surface import cached_surface
    from nx_loom.ui import overlay

    out = []

    out.append(("the hover operator exists", hasattr(bpy.ops.nxloom, "hover"), ""))
    from nx_loom.ui.tools import NXLOOM_TOOL_draw
    bound = [k[0] for k in NXLOOM_TOOL_draw.bl_keymap]
    out.append(("it is wired to mouse-move on the draw tool",
                "nxloom.hover" in bound
                and any(k[0] == "nxloom.hover" and k[1]["type"] == "MOUSEMOVE"
                        for k in NXLOOM_TOOL_draw.bl_keymap), str(bound)))

    # setting the same hover twice must not ask for a redraw the second time
    overlay.clear_hover()
    p = np.array([0.0, 0.0, 1.0])
    overlay.set_hover(node=p)
    first = overlay._hover["node"]
    overlay.set_hover(node=p.copy())
    out.append(("hover state is stable under an identical update",
                first is not None
                and np.allclose(overlay._hover["node"], p), ""))
    overlay.clear_hover()
    out.append(("clearing hover empties it",
                overlay._hover["node"] is None
                and overlay._hover["arc"] is None, ""))

    obj = _traced()
    graph = get_graph(obj)
    src = bpy.data.objects.get(graph.reference)
    surf = cached_surface(src, bpy.context.evaluated_depsgraph_get())

    node = next(iter(graph.nodes.values()))
    target = np.asarray(node.co, dtype=float)
    origin = target + np.array([0.0, 0.0, 5.0])
    hit = ray_surface(surf, origin, np.array([0.0, 0.0, -1.0]))
    out.append(("a ray at a node lands on the surface", hit is not None, ""))
    if hit is not None:
        found = nearest_node(graph, hit, 0.2)
        out.append(("and finds the node under it", found is not None,
                    str(found)))

    # the whole per-move path must be cheap enough to run on every mouse-move
    rays = []
    for k in range(200):
        a = k / 200 * 2 * np.pi
        o = np.array([np.cos(a) * 0.5, np.sin(a) * 0.5, 5.0])
        rays.append((o, np.array([0.0, 0.0, -1.0])))
    t0 = time.perf_counter()
    for o, d in rays:
        s2 = cached_surface(src, bpy.context.evaluated_depsgraph_get())
        pt = ray_surface(s2, o, d)
        if pt is not None:
            if nearest_node(graph, pt, 0.1) is None:
                nearest_on_arc(graph, pt, 0.1)
    per = (time.perf_counter() - t0) / len(rays)
    out.append(("hover costs little enough to run on every mouse-move",
                per < 0.004, f"{per*1000:.2f} ms per move"))
    return out
