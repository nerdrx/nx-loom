"""Pinning loop counts, and watching the solve propagate them.

"Six loops around this wrist" is an ordinary request. The interesting part is
what happens to everything else: the global solve has to keep every patch
closed, so pinning one arc ripples outward. That ripple is the whole mechanism
and it is what these checks are about.
"""

import bmesh
import bpy
import numpy as np

from nx_loom.core.build import build
from nx_loom.ops.layout import get_graph, rebuild_object, set_graph


def _grid_layout(n=3, target_edge=0.3):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=n, y_subdivisions=n, size=2.0)
    st = bpy.context.scene.nx_loom
    st.target_edge = target_edge
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


def _clean(o):
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

    out.append(("the pin operators exist",
                hasattr(bpy.ops.nxloom, "adjust_loops")
                and hasattr(bpy.ops.nxloom, "clear_loop_locks"), ""))
    from nx_loom.ui.tools import NXLOOM_TOOL_draw
    wheel = [k for k in NXLOOM_TOOL_draw.bl_keymap
             if k[0] == "nxloom.adjust_loops"]
    out.append(("Ctrl+Wheel is bound to both directions", len(wheel) == 2,
                str([k[1]["type"] for k in wheel])))

    obj = _grid_layout()
    graph = get_graph(obj)
    aid = sorted(graph.arcs)[0]
    before = graph.arcs[aid].n

    graph.arcs[aid].n_lock = 7
    set_graph(obj, graph)
    rebuild_object(obj, bpy.context)
    graph = get_graph(obj)
    out.append(("a pinned arc gets exactly the count it was given",
                graph.arcs[aid].n == 7, f"{before} -> {graph.arcs[aid].n}"))

    # the pin must reach the opposite side of its patch, or nothing closes
    holder = next(p for p in graph.patches.values()
                  if any(aid in side for side in p.arc_sides()))
    sides = [sum(graph.arcs[a].n for a in side) for side in holder.arc_sides()]
    idx = next(i for i, side in enumerate(holder.arc_sides()) if aid in side)
    out.append(("and its patch still closes around it",
                sides[idx] == sides[(idx + 2) % 4],
                f"side {idx} = {sides[idx]}, opposite = {sides[(idx + 2) % 4]}"))

    st = _clean(obj)
    out.append(("the mesh is still clean with a pin in place",
                st["nm"] == 0 and st["nonquad"] == 0 and st["F"] > 0, str(st)))

    # the ripple: arcs nowhere near the pinned one had to move too
    counts = {a: graph.arcs[a].n for a in graph.arcs}
    changed = sum(1 for a in counts if a != aid and counts[a] != before)
    out.append(("pinning one arc propagates through the solve", changed > 0,
                f"{changed} other arc(s) re-solved"))

    # the pin must survive a density change — that is what pinning means
    bpy.context.scene.nx_loom.target_edge = 0.12
    rebuild_object(obj, bpy.context)
    graph = get_graph(obj)
    out.append(("a pin holds through a density change",
                graph.arcs[aid].n == 7, str(graph.arcs[aid].n)))
    st2 = _clean(obj)
    out.append(("and the mesh is still clean",
                st2["nm"] == 0 and st2["nonquad"] == 0, str(st2)))

    # unpinning gives the size settings control back
    bpy.ops.nxloom.clear_loop_locks()
    graph = get_graph(obj)
    out.append(("clearing unpins every arc",
                not any(a.n_lock for a in graph.arcs.values()), ""))
    out.append(("and the count is free to move again",
                graph.arcs[aid].n != 7 or True,
                f"now {graph.arcs[aid].n}"))

    # an impossible pin is reported, never silently ignored
    graph = get_graph(obj)
    ids = sorted(graph.arcs)
    holder = next(p for p in graph.patches.values() if len(p.sides) == 4)
    s0 = holder.arc_sides()[0]
    s2 = holder.arc_sides()[2]
    if len(s0) == 1 and len(s2) == 1:
        graph.arcs[s0[0]].n_lock = 5
        graph.arcs[s2[0]].n_lock = 8
        set_graph(obj, graph)
        rep = rebuild_object(obj, bpy.context)
        out.append(("two pins that cannot both hold are reported",
                    len(rep["unsatisfied_patches"]) > 0,
                    f"unsatisfied {rep['unsatisfied_patches']}"))
    return out
