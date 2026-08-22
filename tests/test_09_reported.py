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
    return out
