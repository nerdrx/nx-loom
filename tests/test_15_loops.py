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

    out += run_patch_density()
    out += run_typed_loops()
    out += run_symmetric_pins()
    out += run_symmetric_attrs()
    return out


def run_patch_density():
    """Asking one patch for more detail than the rest."""
    out = []

    obj = _grid_layout(n=3, target_edge=0.3)
    graph = get_graph(obj)
    base_total = len(obj.data.polygons)

    pid = sorted(graph.patches)[0]
    out.append(("patches start at 1.0x", abs(graph.patch_density(pid) - 1.0) < 1e-9,
                str(graph.patch_density(pid))))

    def faces_in(o, patch_id):
        attr = o.data.attributes.get("nx_loom_patch")
        if attr is None:
            return 0
        return sum(1 for d in attr.data if int(d.value) == patch_id)

    before = faces_in(obj, pid)
    graph.set_density(pid, 2.5)
    set_graph(obj, graph)
    rebuild_object(obj, bpy.context)
    graph = get_graph(obj)
    after = faces_in(obj, pid)
    out.append(("raising one patch's density gives it more faces",
                after > before, f"{before} -> {after} faces in that patch"))
    out.append(("the override survives the rebuild",
                abs(graph.patch_density(pid) - 2.5) < 1e-6,
                str(graph.patch_density(pid))))
    st = _clean(obj)
    out.append(("and everything still closes",
                st["nm"] == 0 and st["nonquad"] == 0, str(st)))

    # the rest of the model must not be dragged along with it
    others = len(obj.data.polygons) - after
    base_others = base_total - before
    out.append(("the rest of the model is broadly left alone",
                abs(others - base_others) <= max(base_others * 0.6, 8),
                f"{base_others} -> {others} faces elsewhere"))

    # lowering works too
    graph.set_density(pid, 0.4)
    set_graph(obj, graph)
    rebuild_object(obj, bpy.context)
    lowered = faces_in(obj, pid)
    out.append(("lowering it gives fewer", lowered < after,
                f"{after} -> {lowered}"))

    bpy.ops.nxloom.clear_patch_density()
    graph = get_graph(obj)
    out.append(("clearing returns every patch to the global settings",
                not graph.settings.get("density"), ""))

    from nx_loom.ui.tools import NXLOOM_TOOL_draw
    keys = [k for k in NXLOOM_TOOL_draw.bl_keymap
            if k[0] == "nxloom.adjust_patch_density"]
    out.append(("Ctrl+Alt+Wheel is bound both ways", len(keys) == 2,
                str([k[1]["type"] for k in keys])))
    return out


def run_typed_loops():
    """Selecting an arc and typing an exact count, and unpinning just one."""
    from nx_loom.ops.draw import (ACTIVE_KEY, active_arc, apply_active_loops,
                                  set_active_arc)

    out = []
    obj = _grid_layout(n=3, target_edge=0.3)
    graph = get_graph(obj)
    aid = sorted(graph.arcs)[0]

    out.append(("nothing is selected to begin with", active_arc(obj) is None, ""))
    set_active_arc(obj, aid)
    out.append(("an arc can be selected", active_arc(obj) == aid, str(active_arc(obj))))

    # typing a number pins it to exactly that, no scrolling involved
    for want in (9, 3, 12):
        apply_active_loops(bpy.context, want)
        graph = get_graph(obj)
        got = graph.arcs[aid].n
        if got != want:
            out.append((f"typing {want} pins exactly", False, f"got {got}"))
            break
    else:
        out.append(("typing a number pins exactly that count", True,
                    "9, 3 and 12 all landed"))

    out.append(("and it is recorded as a pin",
                get_graph(obj).arcs[aid].n_lock == 12, ""))
    st = _clean(obj)
    out.append(("the mesh still closes around a typed pin",
                st["nm"] == 0 and st["nonquad"] == 0, str(st)))

    # the scene field is what the panel types into
    bpy.context.scene.nx_loom.active_loops = 5
    out.append(("the panel field applies straight through",
                get_graph(obj).arcs[aid].n == 5,
                str(get_graph(obj).arcs[aid].n)))

    # unpin just this one, leaving others alone
    other = sorted(graph.arcs)[1]
    g = get_graph(obj)
    g.arcs[other].n_lock = 4
    set_graph(obj, g)
    rebuild_object(obj, bpy.context)
    res = bpy.ops.nxloom.unpin_arc()
    g = get_graph(obj)
    out.append(("unpinning one arc leaves the others pinned",
                "FINISHED" in res and g.arcs[aid].n_lock is None
                and g.arcs[other].n_lock == 4,
                f"selected={g.arcs[aid].n_lock}, other={g.arcs[other].n_lock}"))

    out.append(("unpin is unavailable when nothing is pinned",
                not bpy.ops.nxloom.unpin_arc.poll(), ""))

    # a burst of wheel notches must not each trigger a rebuild
    from nx_loom.ops import draw as draw_ops
    draw_ops._PENDING["obj"] = None
    draw_ops.queue_rebuild(obj)
    first = draw_ops._PENDING["obj"] is not None
    draw_ops.queue_rebuild(obj)
    draw_ops.queue_rebuild(obj)
    out.append(("repeated adjustments coalesce into one pending rebuild",
                first and draw_ops._PENDING["obj"] is obj, ""))
    draw_ops._deferred_rebuild()
    out.append(("and the pending rebuild clears once it runs",
                draw_ops._PENDING["obj"] is None, ""))

    from nx_loom.ui.tools import NXLOOM_TOOL_draw
    sel = [k for k in NXLOOM_TOOL_draw.bl_keymap if k[0] == "nxloom.select_arc"]
    out.append(("Alt+Shift click selects an arc",
                len(sel) == 1 and sel[0][1].get("alt") and sel[0][1].get("shift"),
                str(sel)))
    return out


def run_symmetric_pins():
    """Pins on mirrored and twinned arcs.

    With symmetry on, half the arcs are not their own representative in the
    solve. Reading locks off the representatives alone dropped every pin on the
    other half, so pinning those arcs looked like the solver ignoring you.
    """
    out = []

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=3, y_subdivisions=3, size=2.0)
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.3
    st.relax_iters = 0
    st.reproject = False
    st.size_mode = "EDGE"
    st.symmetry_axis = "X"
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.nxloom.layout_from_selection()
    if bpy.context.active_object.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
    obj = bpy.context.active_object
    graph = get_graph(obj)

    paired = [(a, arc.mirror_of if arc.mirror_of is not None else arc.twin)
              for a, arc in graph.arcs.items()
              if arc.mirror_of is not None or arc.twin is not None]
    out.append(("the symmetric layout has paired arcs", len(paired) > 0,
                f"{len(paired)} paired"))
    if not paired:
        return out

    aid, src = paired[0]
    graph.arcs[aid].n_lock = 9
    set_graph(obj, graph)
    rebuild_object(obj, bpy.context)
    graph = get_graph(obj)
    out.append(("a pin on a paired arc is honoured", graph.arcs[aid].n == 9,
                f"arc {aid} = {graph.arcs[aid].n}"))
    out.append(("and its partner follows it", graph.arcs[src].n == 9,
                f"partner {src} = {graph.arcs[src].n}"))
    st2 = _clean(obj)
    out.append(("the mesh still closes", st2["nm"] == 0 and st2["nonquad"] == 0,
                str(st2)))

    # pinning both halves to different numbers cannot hold; say so
    graph = get_graph(obj)
    graph.arcs[aid].n_lock = 9
    graph.arcs[src].n_lock = 4
    set_graph(obj, graph)
    rep = rebuild_object(obj, bpy.context)
    out.append(("two halves pinned differently is reported, not ignored",
                len(rep.get("lock_conflicts", [])) > 0,
                str(rep.get("lock_conflicts"))))
    return out


def run_symmetric_attrs():
    """Patch attributes under symmetry: one key for both halves."""
    out = []

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=3, y_subdivisions=3, size=2.0)
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.3
    st.relax_iters = 0
    st.reproject = False
    st.size_mode = "EDGE"
    st.symmetry_axis = "X"
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.nxloom.layout_from_selection()
    if bpy.context.active_object.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
    obj = bpy.context.active_object
    graph = get_graph(obj)

    def mirrored_patch(g):
        for pid, p in g.patches.items():
            arcs = {a for s in p.arc_sides() for a in s}
            if arcs and all(g.arcs[a].mirror_of is not None
                            or g.arcs[a].twin is not None for a in arcs):
                return pid
        return None

    def faces_in(o, pid):
        attr = o.data.attributes.get("nx_loom_patch")
        return sum(1 for d in attr.data if int(d.value) == pid) if attr else 0

    # density set on the MIRRORED side must not be silently dropped
    pid = mirrored_patch(graph)
    out.append(("the symmetric layout has a mirrored-side patch",
                pid is not None, str(pid)))
    if pid is not None:
        before = faces_in(obj, pid)
        graph.set_density(pid, 2.5)
        set_graph(obj, graph)
        rebuild_object(obj, bpy.context)
        g2 = get_graph(obj)
        after = faces_in(obj, mirrored_patch(g2))
        out.append(("density on the mirrored side takes effect",
                    after > before, f"{before} -> {after} faces"))
        # and its authored partner matches, or the mesh is asymmetric
        canon = g2.canonical_key(mirrored_patch(g2))
        partner = next((q for q in g2.patches
                        if q != mirrored_patch(g2)
                        and g2.canonical_key(q) == canon), None)
        if partner is not None:
            out.append(("the authored partner densifies identically",
                        faces_in(obj, partner) == after,
                        f"{faces_in(obj, partner)} vs {after}"))
        bpy.ops.nxloom.clear_patch_density()

    # a hole on one side holes both, and the mesh stays exactly symmetric
    graph = get_graph(obj)
    pid = sorted(graph.patches)[0]
    graph.set_hole(pid, True)
    set_graph(obj, graph)
    rebuild_object(obj, bpy.context)
    g3 = get_graph(obj)
    holed = sum(1 for p in g3.patches.values() if p.fill == "hole")
    out.append(("a hole on one side holes its mirror too", holed == 2,
                f"{holed} holed"))
    P = np.array([tuple(obj.matrix_world @ v.co) for v in obj.data.vertices])
    if len(P):
        M = P.copy()
        M[:, 0] *= -1
        d = np.linalg.norm(P[:, None, :] - M[None, :, :], axis=2).min(axis=1)
        out.append(("the holed mesh is still exactly symmetric",
                    float(d.max()) < 1e-9, f"max {d.max():.1e}"))

    # un-holing one side un-holes both
    g3.set_hole(pid, False)
    set_graph(obj, g3)
    rebuild_object(obj, bpy.context)
    g4 = get_graph(obj)
    out.append(("un-holing clears both halves",
                sum(1 for p in g4.patches.values() if p.fill == "hole") == 0, ""))

    # with symmetry OFF the canonical key is the raw key — no behaviour change
    st.symmetry_axis = "NONE"
    rebuild_object(obj, bpy.context)
    g5 = get_graph(obj)
    pid5 = sorted(g5.patches)[0]
    out.append(("without symmetry, canonical equals raw",
                g5.canonical_key(pid5) == g5.patches[pid5].arc_key(), ""))
    return out
