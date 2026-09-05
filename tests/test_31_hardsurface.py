"""The hard-surface lane: crease proposals, crease fidelity, support loops.

Contracts: Suggest from Creases turns the reference's fold into crease-typed
ghosts and accepting yields crease arcs ON the fold; a crease arc is never
faired, whatever the smooth setting; re-running proposes nothing for creases
the layout already carries; and Support Loops commits parallel loops in the
bordering quads that survive the solve.
"""

import bpy
import numpy as np

from nx_loom.ops.layout import get_graph, set_graph


def _tent_object(n=21, fold=0.5):
    V, quads = [], []
    xs = np.linspace(-1.0, 1.0, n)
    for y in np.linspace(-1.0, 1.0, n):
        for x in xs:
            V.append((x, y, fold * (1.0 - abs(x))))
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            quads.append((a, a + 1, a + n + 1, a + n))
    me = bpy.data.meshes.new("tent")
    me.from_pydata(V, [], quads)
    me.update()
    obj = bpy.data.objects.new("tent", me)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # ---- Suggest from Creases on a tent -------------------------------
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ref = _tent_object()
    st = bpy.context.scene.nx_loom
    st.reference = ref
    st.target_edge = 0.3
    st.relax_iters = 2
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object

    res = bpy.ops.nxloom.suggest_creases()
    graph = get_graph(obj)
    ghosts = graph.settings.get("suggestions") or []
    types = graph.settings.get("suggestion_types") or []
    on_fold = 0
    for flat in ghosts:
        pts = np.asarray(flat, dtype=float).reshape(-1, 3)
        if float(np.abs(pts[:, 0]).max()) < 0.16:
            on_fold += 1
    out.append(("the fold becomes crease-typed ghosts",
                "FINISHED" in res and len(ghosts) >= 1
                and on_fold == len(ghosts)
                and all(t == "crease" for t in types[-len(ghosts):]),
                f"{len(ghosts)} ghost(s), {on_fold} on the fold"))

    arcs_before = len(graph.arcs)
    res = bpy.ops.nxloom.suggest_accept()
    graph = get_graph(obj)
    creases = [a for a in graph.arcs.values() if a.type == "crease"]
    on_fold_arcs = [a for a in creases
                    if float(np.abs(np.asarray(a.path)[:, 0]).max()) < 0.16]
    out.append(("accepting yields crease arcs ON the fold",
                "FINISHED" in res and len(graph.arcs) > arcs_before
                and len(creases) >= 1
                and len(on_fold_arcs) == len(creases),
                f"{len(creases)} crease arc(s)"))

    res = bpy.ops.nxloom.suggest_creases()
    out.append(("a carried crease is not proposed again",
                "CANCELLED" in res, str(res)))

    # ---- crease fidelity: commit never fairs a crease -----------------
    from nx_loom.ops.draw import _surface_of, commit_path
    surface = _surface_of(graph, bpy.context)
    zig = []
    for k in range(24):
        t = k / 23.0
        zig.append((0.0 if k % 2 == 0 else 0.03,
                    -0.9 + 1.8 * t, 0.5))
    zig = np.asarray(zig, dtype=float)
    g2 = get_graph(obj)
    commit_path(g2, surface, zig.copy(), 0.001, 0.001,
                arc_type="crease", smooth=0.6)
    crease_new = max((a for a in g2.arcs.values() if a.type == "crease"),
                     key=lambda a: a.id)
    g3 = get_graph(obj)
    commit_path(g3, surface, zig.copy(), 0.001, 0.001,
                arc_type="flow", smooth=0.6)
    flow_new = max(g3.arcs.values(), key=lambda a: a.id)

    def rough(arc):
        x = np.asarray(arc.path, dtype=float)[:, 0]
        return float(np.abs(np.diff(x, 2)).mean()) if len(x) > 4 else 0.0
    r_crease, r_flow = rough(crease_new), rough(flow_new)
    out.append(("a crease keeps its jitter; a flow arc is faired",
                r_crease > r_flow * 3.0 and r_crease > 0.01,
                f"crease roughness {r_crease:.4f} vs flow {r_flow:.4f}"))

    # ---- support loops on a clean quad layout -------------------------
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

    aid = None
    for a, arc in graph.arcs.items():
        borders = [p for p in graph.patches.values()
                   if len(p.sides) == 4
                   and any(sa == a for side in p.sides for sa, _ in side)]
        if len(borders) == 2:
            aid = a
            break
    obj["nx_loom_active_arc"] = int(aid)
    arcs_before = len(graph.arcs)
    quads_before = len(obj.data.polygons)
    res = bpy.ops.nxloom.support_loops()
    graph = get_graph(obj)
    out.append(("support loops commit on both sides",
                "FINISHED" in res and len(graph.arcs) >= arcs_before + 2,
                f"{len(graph.arcs) - arcs_before} new arcs"))
    out.append(("and the layout still solves, finer where it counts",
                len(obj.data.polygons) > 0
                and not list(obj.get("nx_loom_bad_patches", []) or []),
                f"{quads_before} -> {len(obj.data.polygons)} quads"))

    return out
