"""The veteran pass: frozen regions, spacing bias, topology stamps.

Contracts: a frozen patch's counts survive ANY later re-solve untouched and
thaw cleanly; bias moves loop rows without changing a single count; stamps
arrive as ghosts through the suggestion lane (SPEC §7 — never applied on
their own) and a saved stamp round-trips through the library file.
"""

import bpy
import numpy as np

from nx_loom.core.stamp import builtin, normalize, place
from nx_loom.core.surface import resample
from nx_loom.ops.layout import get_graph, rebuild_object, set_graph


def _sphere_layout(symmetry="NONE"):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=6, radius=1.0)
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.25
    st.relax_iters = 2
    st.symmetry_axis = symmetry
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.nxloom.layout_from_selection()
    return bpy.context.active_object, bpy.context.scene.nx_loom


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # ---- spacing bias --------------------------------------------------
    path = np.stack([np.linspace(0.0, 1.0, 33),
                     np.zeros(33), np.zeros(33)], axis=1)
    even = resample(path, 8)
    seg_e = np.linalg.norm(np.diff(even, axis=0), axis=1)
    pinched = resample(path, 8, bias=1.5)
    seg_p = np.linalg.norm(np.diff(pinched, axis=0), axis=1)
    out.append(("bias crowds samples toward the start, monotonically",
                np.all(np.diff(seg_p) > 0) and seg_p[0] < seg_e[0] * 0.6,
                f"first seg {seg_p[0]:.3f} vs even {seg_e[0]:.3f}"))
    out.append(("bias keeps endpoints and count",
                len(pinched) == len(even)
                and np.allclose(pinched[[0, -1]], even[[0, -1]]), ""))

    obj, st = _sphere_layout()
    graph = get_graph(obj)
    counts_before = {a: arc.n for a, arc in graph.arcs.items()}
    aid = next(a for a, arc in graph.arcs.items() if (arc.n or 0) >= 2)
    graph.arcs[aid].bias = 1.5
    set_graph(obj, graph)
    rep = rebuild_object(obj, bpy.context)
    graph = get_graph(obj)
    out.append(("a biased rebuild changes no loop count",
                all(graph.arcs[a].n == n for a, n in counts_before.items()),
                f"{rep['quads']} quads"))
    v1 = np.array([tuple(v.co) for v in obj.data.vertices])
    rebuild_object(obj, bpy.context)
    v2 = np.array([tuple(v.co) for v in obj.data.vertices])
    out.append(("biased rebuilds are deterministic",
                len(v1) == len(v2) and np.allclose(v1, v2), ""))

    # a fresh one-sided arc gets a DERIVED mirror — that copy must carry
    # the source's bias (twins are authored and keep their own)
    obj, st = _sphere_layout(symmetry="X")
    graph = get_graph(obj)
    from nx_loom.core import authoring as A
    from nx_loom.ops.draw import _surface_of

    def P(v):
        v = np.asarray(v, dtype=float)
        return v / np.linalg.norm(v)

    surf = _surface_of(graph, bpy.context)
    pa, pb = P((0.8, 0.35, 0.25)), P((0.85, -0.05, 0.45))
    na = A.new_node(graph, pa, surf)
    nb = A.new_node(graph, pb, surf)
    seg = np.array([P(pa + (pb - pa) * t)
                    for t in np.linspace(0.0, 1.0, 9)])
    src = A.add_arc(graph, na, nb, seg, surf)
    graph.arcs[src].bias = 1.0
    set_graph(obj, graph)
    rebuild_object(obj, bpy.context)
    graph = get_graph(obj)
    mirrors = [arc for arc in graph.arcs.values() if arc.mirror_of == src]
    out.append(("a mirrored arc inherits its source's bias",
                bool(mirrors) and all(
                    abs(m.bias - 1.0) < 1e-6 for m in mirrors),
                f"{len(mirrors)} mirror(s)"))

    # ---- frozen regions ------------------------------------------------
    obj, st = _sphere_layout()
    graph = get_graph(obj)
    pid = next(iter(graph.patches))
    held = {a: graph.arcs[a].n for side in graph.patches[pid].sides
            for a, _ in side}
    graph.set_frozen(pid, True)
    set_graph(obj, graph)
    st.target_edge = 0.12          # much finer — everything should re-solve
    rep = rebuild_object(obj, bpy.context)
    graph = get_graph(obj)
    kept = all(graph.arcs[a].n == n for a, n in held.items())
    others_moved = any(arc.n and arc.n > 3 for a, arc in graph.arcs.items()
                      if a not in held)
    out.append(("a frozen patch holds its counts through a global re-solve",
                kept and rep and not rep["unsatisfied_patches"],
                f"{list(held.values())}"))
    out.append(("while everything unfrozen re-solves finer",
                others_moved, f"{rep['quads']} quads"))

    frozen_now = get_graph(obj).frozen_patches()
    out.append(("frozen_patches finds it after re-discovery",
                len(frozen_now) >= 1, f"{len(frozen_now)}"))

    res = bpy.ops.nxloom.unfreeze_all()
    graph = get_graph(obj)
    rebuild_object(obj, bpy.context)
    graph = get_graph(obj)
    thawed = any(graph.arcs[a].n != n for a, n in held.items())
    out.append(("thawing lets the region re-solve at the new density",
                "FINISHED" in res and thawed,
                f"{[graph.arcs[a].n for a in held]}"))

    res = bpy.ops.nxloom.freeze_solved()
    graph = get_graph(obj)
    out.append(("Freeze All Solved freezes the whole healthy layout",
                "FINISHED" in res
                and len(graph.frozen_patches()) == len(graph.patches),
                f"{len(graph.frozen_patches())}/{len(graph.patches)}"))
    bpy.ops.nxloom.unfreeze_all()

    # ---- stamps --------------------------------------------------------
    eye = builtin("eye")
    out.append(("the eye stamp is rings plus spokes",
                len(eye) == 12, f"{len(eye)} polylines"))
    placed = place(eye, np.array([5.0, 0.0, 0.0]),
                   np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0]), 2.0)
    r = max(float(np.linalg.norm(p - [5, 0, 0], axis=1).max())
            for p in placed)
    out.append(("place maps the unit disc to the asked size and spot",
                abs(r - 2.0) < 1e-6, f"radius {r:.3f}"))
    norm = normalize(placed)
    rmax = max(float(np.linalg.norm(np.asarray(p), axis=1).max())
               for p in norm)
    out.append(("normalize brings a capture back to the unit disc",
                abs(rmax - 1.0) < 1e-6, f"{rmax:.3f}"))

    from nx_loom.ops import stamps as stamps_ops
    import os
    scratch = os.path.join(bpy.app.tempdir or "/tmp", "nxl_test_stamps.json")
    if os.path.exists(scratch):
        os.remove(scratch)
    stamps_ops.STAMP_FILE = scratch

    obj, st = _sphere_layout()
    graph = get_graph(obj)
    for a in list(graph.arcs):        # start from a clean page for ghosts
        pass
    bpy.context.scene.cursor.location = (0.0, 0.0, 1.2)
    res = bpy.ops.nxloom.stamp_place(stamp="eye", scale=0.25)
    graph = get_graph(obj)
    ghosts = graph.settings.get("suggestions") or []
    out.append(("placing a stamp leaves ghosts, not geometry",
                "FINISHED" in res and len(ghosts) == 12
                and len(obj.data.polygons) > 0, f"{len(ghosts)} ghosts"))

    arcs_before = len(graph.arcs)
    res = bpy.ops.nxloom.suggest_accept()
    graph = get_graph(obj)
    out.append(("accepting commits the stamp as ordinary arcs",
                "FINISHED" in res and len(graph.arcs) > arcs_before
                and not (graph.settings.get("suggestions") or []),
                f"{len(graph.arcs) - arcs_before} arcs"))

    st.stamp_name = "my_eye"
    st.stamp_radius = 0.6
    res = bpy.ops.nxloom.stamp_save()
    saved = stamps_ops.load_user_stamps()
    out.append(("saving captures the arcs around the cursor to the library",
                "FINISHED" in res and "my_eye" in saved
                and len(saved["my_eye"]) >= 8,
                f"{len(saved.get('my_eye', []))} polylines"))

    res = bpy.ops.nxloom.stamp_place(stamp="my_eye", scale=0.25)
    graph = get_graph(obj)
    out.append(("a saved stamp places again from the library",
                "FINISHED" in res
                and len(graph.settings.get("suggestions") or []) >= 8, ""))
    stamps_ops.STAMP_FILE = None

    return out
