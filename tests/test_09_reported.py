"""Bugs reported from actual use, and the behaviour that replaced them.

Every case in this file came from someone drawing on a real model, not from a
synthetic fixture. They are the ones worth guarding hardest.
"""

import bmesh
import bpy
import numpy as np

from nx_loom.core import authoring as A
from nx_loom.core.build import background_patches
from nx_loom.core.graph import LayoutGraph
from nx_loom.core.picking import ray_hits, trace_rays
from nx_loom.core.surface import Surface
from nx_loom.ops.draw import commit_arc
from nx_loom.ops.layout import get_graph, rebuild_object, set_graph


def _survey(o):
    bm = bmesh.new()
    bm.from_mesh(o.data)
    d = dict(V=len(bm.verts), F=len(bm.faces),
             nm=sum(1 for e in bm.edges if len(e.link_faces) > 2),
             nonquad=sum(1 for f in bm.faces if len(f.verts) != 4))
    bm.free()
    return d


def _body():
    """A torso with a thin limb — the shape the hull bug was reported on."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=16, radius=1.0,
                                         location=(0, 0, 1.2))
    torso = bpy.context.active_object
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.28, depth=2.4,
                                        location=(0, 0, -0.3))
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.active_object.select_set(True)
    torso.select_set(True)
    bpy.context.view_layer.objects.active = torso
    bpy.ops.object.join()
    return torso


def _ring_rays(a, b, n=8):
    out = []
    for k in range(n + 1):
        t = k / n
        p = np.array(a) * (1 - t) + np.array(b) * t
        o = np.array([p[0] * 6, p[1] * 6, p[2]])
        d = np.array([-p[0], -p[1], 0.0])
        out.append((o, d / np.linalg.norm(d)))
    return out


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # 1. Placing a point before connecting anything used to raise IndexError
    #    out of the rotation system, which made point-first authoring
    #    impossible: the very first click errored.
    g = LayoutGraph()
    n0 = A.new_node(g, [0, 0, 0])
    try:
        r = g.discover_patches(normal_at=lambda p: [0, 0, 1])
        ok = r["patches"] == 0
        msg = str(r)
    except Exception as e:
        ok, msg = False, repr(e)
    out.append(("a lone placed point does not crash discovery", ok, msg))

    n1 = A.new_node(g, [1, 0, 0])
    A.add_arc(g, n0, n1, [[0, 0, 0], [1, 0, 0]])
    A.new_node(g, [2, 2, 0])
    try:
        g.discover_patches(normal_at=lambda p: [0, 0, 1])
        ok, msg = True, ""
    except Exception as e:
        ok, msg = False, repr(e)
    out.append(("a dangling arc plus a loose point does not crash", ok, msg))

    # 2. A stroke must stay on the surface facing you, not jump to whatever is
    #    behind it.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.25, depth=3.0)
    leg = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 2.0, 0))
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.active_object.select_set(True)
    leg.select_set(True)
    bpy.context.view_layer.objects.active = leg
    bpy.ops.object.join()
    surf = Surface(leg, bpy.context.evaluated_depsgraph_get())

    hits = ray_hits(surf, np.array([0.0, -6.0, 0.0]), np.array([0.0, 1.0, 0.0]))
    out.append(("a ray through a limb reports every crossing", len(hits) >= 3,
                f"{len(hits)} hits"))

    rays = [(np.array([x, -6.0, 0.0]), np.array([0.0, 1.0, 0.0]))
            for x in np.linspace(-0.22, 0.22, 25)]
    path = trace_rays(surf, rays)
    on_far = bool((path[:, 1] > 1.0).any()) if len(path) else True
    out.append(("a stroke stays on the near surface", len(path) > 20 and not on_far,
                f"{len(path)} pts, y {path[:, 1].min():.2f}..{path[:, 1].max():.2f}"))

    # 3. A ring round a limb: no junctions, no sharp turns, so it used to be
    #    rejected for having no corners and produced nothing at all.
    body = _body()
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.25
    st.relax_iters = 4
    st.fill_background = False
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object
    surf = Surface(body, bpy.context.evaluated_depsgraph_get())
    graph = get_graph(obj)

    R, z, N = 0.28, -0.9, 16
    ring = [(R * np.cos(2 * np.pi * i / N), R * np.sin(2 * np.pi * i / N), z)
            for i in range(N)]
    for i in range(N):
        commit_arc(graph, surf, _ring_rays(ring[i], ring[(i + 1) % N]), 0.08, 0.02)
    rep = graph.discover_patches(normal_at=surf.normal_at)
    out.append(("a smooth closed ring becomes a patch", rep["patches"] >= 1,
                str(rep)))
    out.append(("the ring patch is a quad patch",
                all(len(p.sides) == 4 for p in graph.patches.values()),
                str([len(p.sides) for p in graph.patches.values()])))

    set_graph(obj, graph)
    build = rebuild_object(obj, bpy.context)
    st_after = _survey(obj)
    # the cap is ~0.25 across on a body ~3.4 tall: it must not cover the hull
    span = max(obj.dimensions) if st_after["F"] else 0.0
    out.append(("the fill lands on the limb, not the whole hull",
                st_after["F"] > 0 and span < 1.0,
                f"{st_after['F']} faces spanning {span:.2f} of a 3.4-tall body"))
    out.append(("and it is clean", st_after["nm"] == 0 and st_after["nonquad"] == 0,
                str(st_after)))

    # 4. Holes: marked by arc identity, so they survive re-discovery and re-grid
    pid = min(graph.patches, key=lambda k: graph.patch_area(k))
    graph = get_graph(obj)
    graph.set_hole(pid, True)
    set_graph(obj, graph)
    r = rebuild_object(obj, bpy.context)
    out.append(("marking a patch a hole removes its faces",
                _survey(obj)["F"] == 0 and pid in r["holes"], str(r["holes"])))

    bpy.context.scene.nx_loom.target_edge = 0.12
    r2 = rebuild_object(obj, bpy.context)
    out.append(("a hole survives a density change",
                _survey(obj)["F"] == 0 and len(r2["holes"]) == 1,
                f"holes {r2['holes']}"))

    bpy.context.scene.nx_loom.target_edge = 0.25
    graph = get_graph(obj)
    graph.set_hole(pid, False)
    set_graph(obj, graph)
    rebuild_object(obj, bpy.context)
    out.append(("un-holing fills it again", _survey(obj)["F"] > 0,
                str(_survey(obj))))

    # 5. Background detection: a region that dwarfs every other one is the
    #    leftover of the model, not something anyone drew.
    g2 = LayoutGraph()
    ids = [A.new_node(g2, p) for p in ([0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0])]
    for i in range(4):
        A.add_arc(g2, ids[i], ids[(i + 1) % 4],
                  [g2.nodes[ids[i]].co, g2.nodes[ids[(i + 1) % 4]].co])
    g2.discover_patches(normal_at=lambda p: [0, 0, 1])
    out.append(("two comparable regions are both kept",
                len(background_patches(g2)) == 0,
                f"{len(g2.patches)} patches"))

    out += run_point_and_drag()
    out += run_move_and_pick()
    return out


def run_point_and_drag():
    """Placing a point, and dragging on a dense mesh. Both reported broken."""
    import time

    from nx_loom.core import symmetry as sym
    from nx_loom.ops.draw import commit_path, refresh
    from nx_loom.ops.layout import set_graph

    out = []

    # A node with no arcs is the point you just placed. Sweeping every orphan
    # on refresh deleted it immediately, which made point-first authoring
    # impossible — the anchor was gone before the second click.
    for axis in ("NONE", "X"):
        g = LayoutGraph()
        nid = A.new_node(g, [0.3, 0.0, 0.95])
        sym.sync(g, axis, 0.002, None)
        out.append((f"a placed point survives a sync (symmetry {axis})",
                    nid in g.nodes, f"{len(g.nodes)} nodes left"))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    src = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.3
    st.relax_iters = 2
    st.symmetry_axis = "NONE"
    st.size_mode = "EDGE"
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object
    surf = Surface(src, bpy.context.evaluated_depsgraph_get())

    # place two points with nothing attached, exactly as clicking does
    graph = get_graph(obj)
    a = A.resolve_anchor(graph, np.array([1.0, 0.0, 0.0]), 0.1, surf)[0]
    refresh(obj, graph, bpy.context, rebuild=False)
    graph = get_graph(obj)
    out.append(("the first point is still there after a refresh",
                a in graph.nodes, f"{len(graph.nodes)} nodes"))

    b = A.resolve_anchor(graph, np.array([0.0, 1.0, 0.0]), 0.1, surf)[0]
    refresh(obj, graph, bpy.context, rebuild=False)
    graph = get_graph(obj)
    out.append(("and so is the second", a in graph.nodes and b in graph.nodes,
                f"{len(graph.nodes)} nodes"))

    # then connect them, which is what the next click does
    path = np.array([[1.0, 0.0, 0.0], [0.7, 0.7, 0.0], [0.0, 1.0, 0.0]])
    res = commit_path(graph, surf, path, 0.1, 0.02, start_node=a)
    out.append(("two placed points can then be joined",
                res is not None and len(graph.arcs) == 1,
                f"{len(graph.arcs)} arcs from {len(graph.nodes)} nodes"))

    # dragging: tracing one new sample per move must match tracing in one go,
    # and must not get slower as the reference gets denser
    rays = []
    for k in range(60):
        t = k / 60 * 1.2 - 0.6
        p = np.array([np.sin(t), np.cos(t), 0.3])
        rays.append((p * 3.0, -p))
    inc = []
    for r in rays:
        got = trace_rays(surf, [r], anchor=inc[-1] if inc else None)
        if len(got):
            inc.append(got[0])
    batch = trace_rays(surf, rays)
    agree = (len(inc) == len(batch)
             and float(np.abs(np.asarray(inc) - batch).max()) < 1e-9)
    out.append(("incremental tracing matches tracing the whole stroke", agree,
                f"{len(inc)} vs {len(batch)} samples"))

    def per_ray(seg, ring):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=seg, ring_count=ring,
                                             radius=1.0)
        s2 = Surface(bpy.context.active_object,
                     bpy.context.evaluated_depsgraph_get())
        t0 = time.perf_counter()
        for r in rays:
            trace_rays(s2, [r])
        return (time.perf_counter() - t0) / len(rays), len(s2.verts)

    fast, n_small = per_ray(24, 12)
    slow, n_big = per_ray(160, 80)
    ratio = slow / max(fast, 1e-9)
    # Recomputing the model's bounding span inside every ray made this scale
    # with vertex count; it is cached on the Surface now.
    out.append(("tracing does not slow down as the sculpt gets denser",
                ratio < 4.0,
                f"{n_small} verts {fast*1e6:.0f}us/ray vs {n_big} verts "
                f"{slow*1e6:.0f}us/ray ({ratio:.1f}x)"))
    return out


def run_move_and_pick():
    """Dragging a node, and how responsive clicking is. Both reported."""
    import time

    from nx_loom.core.surface import (Surface, cached_surface,
                                      clear_surface_cache)

    out = []

    # Moving a node used to rewrite only the polyline's endpoint, leaving every
    # interior sample where it was — so the arc got a spike at the node instead
    # of bending. That is what "moving a node messes up the arc" looks like.
    # An arc made by clicking two points has no shape of its own — it was
    # derived from where the endpoints were. Moving one must re-lay the whole
    # segment, not deform the samples of the old one.
    g = LayoutGraph()
    n0 = A.new_node(g, [0, 0, 0])
    n1 = A.new_node(g, [4, 0, 0])
    straight = np.array([[x, 0.0, 0.0] for x in np.linspace(0, 4, 17)])
    sid = A.add_arc(g, n0, n1, straight.copy(), rail="straight")
    A.move_node(g, n0, [0.0, 1.5, 0.0])
    sp = np.asarray(g.arcs[sid].path)
    t = np.linspace(0, 1, len(sp))[:, None]
    ideal = np.array([0.0, 1.5, 0.0]) * (1 - t) + np.array([4.0, 0.0, 0.0]) * t
    out.append(("a clicked segment moves as a whole, staying straight",
                float(np.linalg.norm(sp - ideal, axis=1).max()) < 1e-9,
                f"deviation {np.linalg.norm(sp - ideal, axis=1).max():.2e}"))

    # A freehand stroke IS the artist's line, so it bends instead.
    g = LayoutGraph()
    n0 = A.new_node(g, [0, 0, 0])
    n1 = A.new_node(g, [4, 0, 0])
    aid = A.add_arc(g, n0, n1, straight.copy(), rail="surface")
    A.move_node(g, n0, [0.0, 1.5, 0.0])

    path = np.asarray(g.arcs[aid].path)
    step = np.linalg.norm(np.diff(path, axis=0), axis=1)
    dirs = np.diff(path, axis=0) / step[:, None]
    turn = np.degrees(np.arccos(np.clip((dirs[:-1] * dirs[1:]).sum(axis=1), -1, 1)))
    out.append(("dragging a node bends a freehand arc instead of spiking it",
                float(turn.max()) < 20.0,
                f"max turn {turn.max():.1f} deg (a spike is ~90)"))

    disp = np.linalg.norm(path - straight, axis=1)
    out.append(("the bend falls off smoothly to the far end",
                bool(np.all(np.diff(disp) <= 1e-9)) and disp[-1] < 1e-9,
                f"{disp[0]:.2f} at the node -> {disp[-1]:.2f} at the far end"))
    out.append(("both endpoints still sit on their nodes",
                np.allclose(path[0], g.nodes[n0].co)
                and np.allclose(path[-1], g.nodes[n1].co), ""))

    # a partial falloff must leave the far half alone
    g2 = LayoutGraph()
    m0 = A.new_node(g2, [0, 0, 0])
    m1 = A.new_node(g2, [4, 0, 0])
    bid = A.add_arc(g2, m0, m1, straight.copy(), rail="surface")
    A.move_node(g2, m0, [0.0, 1.5, 0.0], falloff=0.4)
    d2 = np.linalg.norm(np.asarray(g2.arcs[bid].path) - straight, axis=1)
    out.append(("a partial Bend leaves the far end untouched",
                d2[0] > 1.0 and float(d2[len(d2) // 2:].max()) < 1e-9,
                f"far half max {d2[len(d2)//2:].max():.3f}"))

    # on a real surface the bent arc must stay on the surface and stay pinned
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    src = bpy.context.active_object
    surf = Surface(src, bpy.context.evaluated_depsgraph_get())
    g3 = LayoutGraph()
    p0 = A.new_node(g3, [1.0, 0.0, 0.0], surf)
    p1 = A.new_node(g3, [0.0, 1.0, 0.0], surf)
    arcpts = []
    for t in np.linspace(0, 1, 13):
        v = np.array([np.cos(t * np.pi / 2), np.sin(t * np.pi / 2), 0.0])
        arcpts.append(v)
    cid = A.add_arc(g3, p0, p1, np.array(arcpts), surf, rail="surface")
    target = np.array([0.9, 0.0, 0.44])
    target /= np.linalg.norm(target)
    A.move_node(g3, p0, target, surf)
    moved = np.asarray(g3.arcs[cid].path)
    off = np.abs(np.linalg.norm(moved, axis=1) - 1.0)
    out.append(("the bent arc stays on the surface", float(off.max()) < 0.02,
                f"max radial error {off.max():.4f}"))
    out.append(("and every sample is re-pinned",
                g3.arcs[cid].pins is not None
                and all(pin is not None for pin in g3.arcs[cid].pins), ""))

    # clicking was slow because every click rebuilt the BVH from scratch
    clear_surface_cache()
    dg = bpy.context.evaluated_depsgraph_get()
    t0 = time.perf_counter()
    Surface(src, dg)
    cold = time.perf_counter() - t0
    cached_surface(src, dg)
    t0 = time.perf_counter()
    for _ in range(10):
        cached_surface(src, dg)
    warm = (time.perf_counter() - t0) / 10
    out.append(("repeat clicks reuse the surface instead of rebuilding it",
                warm < cold * 0.1,
                f"build {cold*1000:.0f} ms vs cached {warm*1000:.3f} ms"))

    src.data.vertices[0].co.x += 0.5
    t0 = time.perf_counter()
    cached_surface(src, dg)
    after = time.perf_counter() - t0
    out.append(("but editing the reference invalidates it",
                after > cold * 0.3, f"{after*1000:.0f} ms after an edit"))
    return out
