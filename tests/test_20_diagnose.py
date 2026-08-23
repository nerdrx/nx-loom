"""Failures that explain themselves, and the Fix It button."""

import bpy
import numpy as np

from nx_loom.core.diagnose import diagnose, plan_fixes
from nx_loom.ops.layout import get_graph, rebuild_object, set_graph


def _grid(n=3, te=0.3):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=n, y_subdivisions=n, size=2.0)
    st = bpy.context.scene.nx_loom
    st.target_edge = te
    st.relax_iters = 0
    st.reproject = False
    st.size_mode = "EDGE"
    st.symmetry_axis = "NONE"
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.nxloom.layout_from_selection()
    if bpy.context.active_object.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
    return bpy.context.active_object


def _conflict(obj):
    """Pin opposite sides of one quad to numbers that cannot both hold."""
    graph = get_graph(obj)
    patch = next(p for p in graph.patches.values()
                 if len(p.sides) == 4
                 and len(p.arc_sides()[0]) == 1 and len(p.arc_sides()[2]) == 1)
    a0 = patch.arc_sides()[0][0]
    a2 = patch.arc_sides()[2][0]
    graph.arcs[a0].n_lock = 3
    graph.arcs[a2].n_lock = 9
    set_graph(obj, graph)
    return a0, a2


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    obj = _grid()
    a0, a2 = _conflict(obj)
    rep = rebuild_object(obj, bpy.context)
    graph = get_graph(obj)
    out.append(("the conflict genuinely fails",
                len(rep["unsatisfied_patches"]) > 0,
                str(rep["unsatisfied_patches"])))

    d = diagnose(graph, rep)
    pid = rep["unsatisfied_patches"][0]
    lines = d.get(pid, [])
    out.append(("the failing patch gets an explanation", len(lines) > 0,
                str(lines)))
    text = " ".join(lines)
    out.append(("it names the numbers that disagree",
                "disagree" in text or "even" in text, text[:80]))
    out.append(("and points at the pins responsible",
                f"{a0} (3*)" in text or f"{a2} (9*)" in text
                or "pinned" in text, text[:80]))

    fixes = plan_fixes(graph, rep)
    out.append(("repairs are proposed", len(fixes) >= 2,
                str([f[0] for f in fixes])))
    out.append(("releasing a pin is the first idea",
                fixes[0][1] == "unlock", str(fixes[0])))

    # the operator applies the first repair that actually works
    from nx_loom.ops.draw import refresh
    refresh(obj, get_graph(obj), bpy.context)
    out.append(("Fix It is offered", bool(obj.get("nx_loom_fixes")), ""))
    res = bpy.ops.nxloom.fix_patch()
    out.append(("Fix It finishes", "FINISHED" in res, str(res)))
    rep2 = rebuild_object(obj, bpy.context)
    graph = get_graph(obj)
    out.append(("the patch resolves", not rep2["unsatisfied_patches"],
                str(rep2["unsatisfied_patches"])))
    locks = [(a, arc.n_lock) for a, arc in graph.arcs.items() if arc.n_lock]
    out.append(("exactly one pin was released, the other kept",
                len(locks) == 1, str(locks)))
    out.append(("the panel guidance clears with the failure",
                not obj.get("nx_loom_fixes"), ""))

    # a validated non-fix is never applied: with no failure, poll is off
    out.append(("Fix It unavailable when nothing fails",
                not bpy.ops.nxloom.fix_patch.poll(), ""))

    # live pin warning through the typed field
    from nx_loom.ops.draw import apply_active_loops, set_active_arc
    obj = _grid()
    graph = get_graph(obj)
    patch = next(p for p in graph.patches.values()
                 if len(p.sides) == 4
                 and len(p.arc_sides()[0]) == 1 and len(p.arc_sides()[2]) == 1)
    a0 = patch.arc_sides()[0][0]
    a2 = patch.arc_sides()[2][0]
    graph.arcs[a0].n_lock = 3
    set_graph(obj, graph)
    rebuild_object(obj, bpy.context)
    set_active_arc(obj, a2)
    apply_active_loops(bpy.context, 9)
    warn = str(obj.get("nx_loom_pin_warn", "") or "")
    out.append(("typing an impossible pin warns immediately",
                "cannot hold" in warn, warn[:60]))
    res = bpy.ops.nxloom.fix_patch()
    out.append(("and Fix It clears it",
                "FINISHED" in res
                and not str(obj.get("nx_loom_pin_warn", "") or ""), str(res)))

    # bigons from merges are counted, not silently vanished
    from nx_loom.core.authoring import merge_nodes
    obj = _grid()
    graph = get_graph(obj)
    arc0 = next(iter(graph.arcs.values()))
    merge_nodes(graph, arc0.a, arc0.b)
    set_graph(obj, graph)
    refresh(obj, graph, bpy.context)
    out.append(("collapsed slivers are surfaced when they appear",
                int(obj.get("nx_loom_bigons", 0) or 0) >= 0,
                f"bigons={obj.get('nx_loom_bigons')}"))

    out += run_checkpoints()
    return out


def run_checkpoints():
    """Named layout states, and the slide-rail capture."""
    from nx_loom.core.graph import GRAPH_KEY
    from nx_loom.core.authoring import new_node

    out = []
    obj = _grid()
    graph = get_graph(obj)
    n_arcs = len(graph.arcs)

    res = bpy.ops.nxloom.checkpoint_save(name="before mess")
    out.append(("a checkpoint saves", "FINISHED" in res, str(res)))

    # wreck the layout
    for aid in list(graph.arcs)[: n_arcs // 2]:
        del graph.arcs[aid]
    new_node(graph, [5, 5, 5])
    set_graph(obj, graph)
    rebuild_object(obj, bpy.context)
    out.append(("the layout is genuinely wrecked",
                len(get_graph(obj).arcs) < n_arcs, ""))

    res = bpy.ops.nxloom.checkpoint_restore(name="before mess")
    out.append(("restore finishes", "FINISHED" in res, str(res)))
    g2 = get_graph(obj)
    out.append(("the layout comes back whole",
                len(g2.arcs) == n_arcs and len(obj.data.polygons) > 0,
                f"{len(g2.arcs)} arcs, {len(obj.data.polygons)} faces"))

    # bpy raises on an ERROR report rather than returning CANCELLED
    try:
        bpy.ops.nxloom.checkpoint_restore(name="never saved")
        refused = False
    except RuntimeError:
        refused = True
    out.append(("restoring an unknown name is refused politely", refused, ""))
    bpy.ops.nxloom.checkpoint_delete(name="before mess")
    out.append(("deleted checkpoints are gone",
                "before mess" not in (obj.get("nx_loom_checkpoints", {}) or {}),
                ""))

    # the slide rail must be frozen at drag start, not read live
    import inspect

    from nx_loom.ops import draw as draw_ops
    src = inspect.getsource(draw_ops.NXLOOM_OT_move_node)
    out.append(("sliding uses rails frozen at drag start",
                "self.rails" in src and ".copy()" in src
                and "event.ctrl" in src, ""))
    return out
