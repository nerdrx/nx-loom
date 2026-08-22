"""Line quality and the halo gesture.

A wobbly stroke becomes a wobbly edge loop in every mesh generated from it
forever, so jitter is faired out at commit. Halos are the eye-socket gesture:
drag outward from a point, get a ring; two concentric halos bridge into a loop
band, which IS the eye topology.
"""

import bmesh
import bpy
import numpy as np

from nx_loom.core.authoring import fair_path
from nx_loom.core.surface import Surface
from nx_loom.ops.draw import commit_halo, commit_path
from nx_loom.ops.layout import get_graph, rebuild_object, set_graph


def _rough(p):
    p = np.asarray(p, dtype=float)
    return float(np.linalg.norm(p[:-2] + p[2:] - 2 * p[1:-1]))


def _sphere_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=1.0)
    src = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.15
    st.relax_iters = 2
    st.symmetry_axis = "NONE"
    st.size_mode = "EDGE"
    bpy.ops.nxloom.new_layout()
    return src, bpy.context.active_object, \
        Surface(src, bpy.context.evaluated_depsgraph_get())


def _ray_at(p):
    p = np.asarray(p, dtype=float)
    return (p * 3.0, -p / np.linalg.norm(p))


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # -- commit-time fairing
    src, obj, surf = _sphere_scene()
    graph = get_graph(obj)
    rng = np.random.default_rng(5)
    t = np.linspace(0, np.pi / 2, 40)
    stroke = np.stack([np.cos(t), np.sin(t), np.zeros_like(t)], axis=1)
    stroke += rng.normal(scale=0.008, size=stroke.shape)
    stroke = stroke / np.linalg.norm(stroke, axis=1, keepdims=True)

    res = commit_path(graph, surf, stroke.copy(), 0.05, 0.005, smooth=0.35)
    aid = res[0]
    smoothed = np.asarray(graph.arcs[aid].path)
    out.append(("a jittery stroke commits smoother than it was drawn",
                _rough(smoothed) < _rough(stroke) * 0.5,
                f"roughness {_rough(stroke):.3f} -> {_rough(smoothed):.3f}"))
    dev = float(np.abs(np.linalg.norm(smoothed, axis=1) - 1.0).max())
    out.append(("and stays on the surface", dev < 0.01, f"max {dev:.4f}"))

    res2 = commit_path(graph, surf, stroke.copy(), 0.05, 0.005, smooth=0.0)
    raw = np.asarray(graph.arcs[res2[0]].path)
    out.append(("smoothing 0 keeps every wobble",
                _rough(raw) > _rough(smoothed) * 2, f"{_rough(raw):.3f}"))

    # -- retroactive Smooth Arcs
    set_graph(obj, graph)
    before = _rough(np.asarray(get_graph(obj).arcs[res2[0]].path))
    bpy.ops.nxloom.smooth_arcs()
    after = _rough(np.asarray(get_graph(obj).arcs[res2[0]].path))
    out.append(("Smooth Arcs fairs existing arcs in place", after < before * 0.5,
                f"{before:.3f} -> {after:.3f}"))
    out.append(("smoothed arcs keep their pins",
                all(p is not None
                    for p in get_graph(obj).arcs[res2[0]].pins or []), ""))

    # -- halo: a ring around a point on the sphere
    src, obj, surf = _sphere_scene()
    graph = get_graph(obj)
    center = np.array([0.0, 0.0, 1.0])
    edge = np.array([0.25, 0.0, 1.0])
    edge = edge / np.linalg.norm(edge)
    res = commit_halo(graph, surf, _ray_at(center), _ray_at(edge))
    out.append(("a drag from a point makes a halo",
                res is not None and len(graph.nodes) == 4
                and len(graph.arcs) == 4,
                f"{len(graph.nodes)}n {len(graph.arcs)}a"))
    if res is None:
        return out
    halo1 = res[0]
    P = np.array([graph.nodes[n].co for n in halo1])
    d = np.linalg.norm(P - center, axis=1)
    out.append(("its nodes sit evenly around the centre",
                float(d.max() - d.min()) < 0.05 and abs(float(d.mean()) - 0.25) < 0.08,
                f"radii {d.min():.3f}..{d.max():.3f}"))
    out.append(("the first node lands where the drag released",
                float(np.linalg.norm(P[0] - edge)) < 0.06, ""))
    out.append(("halo nodes are on the sphere",
                float(np.abs(np.linalg.norm(P, axis=1) - 1).max()) < 0.01, ""))

    # -- two concentric halos bridge into a loop band: eye topology
    edge2 = np.array([0.45, 0.0, 1.0])
    edge2 = edge2 / np.linalg.norm(edge2)
    res2 = commit_halo(graph, surf, _ray_at(center), _ray_at(edge2),
                       bridge_to=halo1)
    out.append(("a concentric halo bridges to the inner one",
                res2 is not None and res2[2] is not None and len(res2[2]) == 4,
                f"{len(graph.arcs)} arcs total"))
    set_graph(obj, graph)
    graph.discover_patches(normal_at=surf.normal_at)
    set_graph(obj, graph)
    rebuild_object(obj, bpy.context)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    nm = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    nq = sum(1 for f in bm.faces if len(f.verts) != 4)
    F = len(bm.faces)
    bm.free()
    out.append(("the band between them builds as clean quads",
                F > 0 and nm == 0 and nq == 0, f"{F} faces"))

    # the inner disc: mark it a hole and the eye socket is open
    g = get_graph(obj)
    inner = min(g.patches, key=lambda p: g.patch_area(p))
    g.set_hole(inner, True)
    set_graph(obj, g)
    rebuild_object(obj, bpy.context)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bnd = sum(1 for e in bm.edges if len(e.link_faces) == 1)
    bm.free()
    out.append(("holing the inner disc opens the socket", bnd > 0,
                f"{bnd} boundary edges"))
    return out
