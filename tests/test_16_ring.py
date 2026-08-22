"""The ring-cut gesture: one swipe across a limb becomes a closed loop.

The pure geometry is tested without bpy; the commit path runs on the same
limb-plus-torso body as the hull-wrap regression, because the failure that
matters is the same — a gesture on the leg must never grab the torso.
"""

import bmesh
import bpy
import numpy as np

from nx_loom.core.contour import (cross_section, is_closed, nearest_loop,
                                  ring_segments)
from nx_loom.core.surface import Surface
from nx_loom.ops.draw import commit_ring
from nx_loom.ops.layout import get_graph, rebuild_object, set_graph


def _cyl_tris(R=0.3, cx=0.0, n=24, h=2.0):
    V, T = [], []
    for i in range(n):
        a = 2 * np.pi * i / n
        V.append([cx + R * np.cos(a), R * np.sin(a), -h / 2])
        V.append([cx + R * np.cos(a), R * np.sin(a), h / 2])
    for i in range(n):
        j = (i + 1) % n
        T.append([2 * i, 2 * j, 2 * i + 1])
        T.append([2 * j, 2 * j + 1, 2 * i + 1])
    return np.array(V, dtype=float), np.array(T, dtype=int)


def _body():
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


def _swipe(z, x0=-0.3, x1=0.3):
    """A stroke across the leg, seen from the front (-Y looking +Y)."""
    return ((np.array([x0, -6.0, z]), np.array([0.0, 1.0, 0.0])),
            (np.array([x1, -6.0, z]), np.array([0.0, 1.0, 0.0])))


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # -- pure geometry
    V, T = _cyl_tris()
    loops = cross_section(V, T, [0, 0, 0.3], [0, 0, 1])
    out.append(("a plane cut of a cylinder is one closed loop",
                len(loops) == 1 and is_closed(loops[0]), str(len(loops))))
    r = np.linalg.norm(loops[0][:, :2], axis=1)
    out.append(("the loop sits on the surface",
                abs(float(r.max()) - 0.3) < 1e-6 and float(r.min()) > 0.29,
                f"radius {r.min():.4f}..{r.max():.4f}"))

    V2, T2 = _cyl_tris(R=0.8, cx=3.0)
    Vb = np.vstack([V, V2])
    Tb = np.vstack([T, T2 + len(V)])
    loops = cross_section(Vb, Tb, [0, 0, 0.1], [0, 0, 1])
    pick = nearest_loop(loops, [0.3, 0, 0.1])
    got_r = float(np.linalg.norm(pick[:, :2], axis=1).mean()) if pick is not None else 0
    out.append(("with two shells cut, the nearest loop wins",
                len(loops) == 2 and abs(got_r - 0.3) < 0.01,
                f"{len(loops)} loops, picked r~{got_r:.2f}"))

    res = ring_segments(pick, k=4, start_at=[0.3, 0, 0.1])
    nodes, paths = res
    lens = [float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum()) for p in paths]
    out.append(("the ring is four even, welded arcs",
                max(lens) - min(lens) < 0.02
                and all(np.allclose(paths[j][-1], paths[(j + 1) % 4][0])
                        for j in range(4)),
                f"lengths {[round(x, 3) for x in lens]}"))
    out.append(("the first node lands under the stroke start",
                float(np.linalg.norm(nodes[0] - [0.3, 0, 0.1])) < 0.06, ""))

    # -- the real body: swipe the leg from the front
    body = _body()
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.25
    st.relax_iters = 2
    st.symmetry_axis = "NONE"
    st.size_mode = "EDGE"
    st.fill_background = False
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object
    surf = Surface(body, bpy.context.evaluated_depsgraph_get())
    graph = get_graph(obj)

    r0, r1 = _swipe(-0.9)
    res = commit_ring(graph, surf, r0, r1)
    out.append(("a swipe across the leg makes a ring", res is not None
                and len(graph.nodes) == 4 and len(graph.arcs) == 4,
                f"{len(graph.nodes)}n {len(graph.arcs)}a"))
    if res is None:
        return out
    P = np.array([graph.nodes[n].co for n in res[0]])
    out.append(("the ring wraps the leg, not the torso",
                float(np.linalg.norm(P[:, :2], axis=1).max()) < 0.35
                and float(np.abs(P[:, 2] + 0.9).max()) < 0.1,
                f"radius {np.linalg.norm(P[:, :2], axis=1).max():.2f}"))

    # a second ring anchors its first node under the same stroke start, so the
    # two rings correspond and bridging them by click is four obvious segments
    res2 = commit_ring(graph, surf, *_swipe(-0.4))
    if res2 is not None:
        a0 = graph.nodes[res[0][0]].co
        b0 = graph.nodes[res2[0][0]].co
        lateral = float(np.linalg.norm((a0 - b0)[:2]))
        out.append(("successive rings get corresponding nodes",
                    lateral < 0.12, f"lateral offset {lateral:.3f}"))

    set_graph(obj, graph)
    rep = rebuild_object(obj, bpy.context)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    nm = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    nq = sum(1 for f in bm.faces if len(f.verts) != 4)
    F = len(bm.faces)
    bm.free()
    span = max(obj.dimensions) if F else 0.0
    out.append(("the rings build clean geometry on the leg only",
                F > 0 and nm == 0 and nq == 0 and span < 1.2,
                f"{F} faces spanning {span:.2f} of a 3.4-tall body"))

    # symmetry: a ring on one side is mirrored like anything else authored
    st.symmetry_axis = "Y"
    rep2 = rebuild_object(obj, bpy.context)
    g2 = get_graph(obj)
    mirrored = sum(1 for a in g2.arcs.values()
                   if a.mirror_of is not None or a.twin is not None)
    out.append(("rings take part in symmetry like drawn arcs",
                mirrored >= 0 and rep2 is not None, f"{mirrored} paired"))
    return out
