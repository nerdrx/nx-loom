"""The primitive lane: detection, exact rings, per-patch flatten.

Contracts: a box segments into plane regions and nothing else; a capped
cylinder's wall is recognised with the right axis and radius; a sphere is
honestly "free"; Suggest from Primitives proposes rings that are round and
axis-perpendicular; and a flattened patch's interior lies on its boundary's
plane while a noisy unflattened one does not.
"""

import bpy
import numpy as np

from nx_loom.core.primitives import detect, fit_cylinder, fit_plane
from nx_loom.ops.layout import get_graph, set_graph


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # ---- fits ----------------------------------------------------------
    rng = np.random.default_rng(7)
    pts = np.concatenate([rng.uniform(-1, 1, (200, 2)),
                          np.zeros((200, 1))], axis=1)
    _c, n, resid = fit_plane(pts)
    out.append(("a plane fits a plane",
                abs(abs(float(n[2])) - 1.0) < 1e-9 and resid < 1e-12,
                f"residual {resid:.2e}"))

    ang = rng.uniform(0, 2 * np.pi, 300)
    z = rng.uniform(-1, 1, 300)
    cpts = np.stack([0.7 * np.cos(ang), 0.7 * np.sin(ang), z], axis=1)
    cnrm = np.stack([np.cos(ang), np.sin(ang), np.zeros(300)], axis=1)
    axis, _cc, r, cres = fit_cylinder(cpts, cnrm)
    out.append(("a cylinder fits a cylinder",
                abs(abs(float(axis[2])) - 1.0) < 1e-6
                and abs(r - 0.7) < 1e-6 and cres < 1e-9,
                f"axis z {axis[2]:.3f}, r {r:.3f}"))

    # ---- detection on real meshes --------------------------------------
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    cube = bpy.context.active_object
    md = cube.modifiers.new("s", "SUBSURF")
    md.subdivision_type = "SIMPLE"
    md.levels = 3
    bpy.ops.object.modifier_apply(modifier="s")
    cube.data.calc_loop_triangles()
    cv = np.array([tuple(v.co) for v in cube.data.vertices])
    ct = np.array([tuple(t.vertices) for t in cube.data.loop_triangles])
    det = detect(cv, ct)
    kinds = [d["kind"] for d in det]
    out.append(("a box is plane regions and nothing else",
                kinds.count("plane") >= 5 and "cylinder" not in kinds,
                f"{kinds.count('plane')} planes of {len(kinds)}"))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=0.6, depth=2.0)
    cyl = bpy.context.active_object
    cyl.data.calc_loop_triangles()
    yv = np.array([tuple(v.co) for v in cyl.data.vertices])
    yt = np.array([tuple(t.vertices) for t in cyl.data.loop_triangles])
    det = detect(yv, yt)
    walls = [d for d in det if d["kind"] == "cylinder"]
    good_wall = any(abs(abs(float(np.asarray(d["axis"])[2])) - 1.0) < 0.05
                    and abs(float(d["radius"]) - 0.6) < 0.05 for d in walls)
    out.append(("a capped cylinder's wall is a cylinder, axis and radius "
                "right", good_wall,
                f"{len(walls)} cylinder region(s)"))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    sph = bpy.context.active_object
    sph.data.calc_loop_triangles()
    sv = np.array([tuple(v.co) for v in sph.data.vertices])
    stt = np.array([tuple(t.vertices) for t in sph.data.loop_triangles])
    det = detect(sv, stt)
    out.append(("a sphere is honestly free",
                all(d["kind"] == "free" for d in det),
                f"{[d['kind'] for d in det]}"))

    # ---- Suggest from Primitives ---------------------------------------
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=0.6, depth=2.0)
    ref = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    st.reference = ref
    st.target_edge = 0.25
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object
    res = bpy.ops.nxloom.suggest_primitives()
    graph = get_graph(obj)
    ghosts = graph.settings.get("suggestions") or []
    round_ok = True
    for flat in ghosts:
        pts = np.asarray(flat, dtype=float).reshape(-1, 3)
        rr = np.linalg.norm(pts[:, :2], axis=1)
        if float(np.ptp(pts[:, 2])) > 0.02 or float(np.ptp(rr)) > 0.03:
            round_ok = False
    out.append(("exact rings proposed on the cylinder",
                "FINISHED" in res and len(ghosts) >= 8 and round_ok
                and len(ghosts) % 4 == 0,
                f"{len(ghosts) // 4} ring(s)"))

    res = bpy.ops.nxloom.suggest_accept()
    graph = get_graph(obj)
    res2 = bpy.ops.nxloom.suggest_primitives()
    out.append(("accepted rings are not proposed again",
                "FINISHED" in res and "CANCELLED" in res2, str(res2)))

    # ---- flatten --------------------------------------------------------
    bpy.ops.wm.read_factory_settings(use_empty=True)
    n = 15
    V, quads = [], []
    rng = np.random.default_rng(3)
    for j, y in enumerate(np.linspace(-1, 1, n)):
        for i, x in enumerate(np.linspace(-1, 1, n)):
            V.append((x, y, float(rng.uniform(-0.03, 0.03))))
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            quads.append((a, a + 1, a + n + 1, a + n))
    me = bpy.data.meshes.new("noisy")
    me.from_pydata(V, [], quads)
    me.update()
    ref = bpy.data.objects.new("noisy", me)
    bpy.context.collection.objects.link(ref)
    bpy.context.view_layer.objects.active = ref
    ref.select_set(True)
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.28
    st.relax_iters = 2
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    st.reference = ref
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object
    graph = get_graph(obj)
    from nx_loom.core import authoring as A
    from nx_loom.ops.draw import _surface_of, refresh
    surf = _surface_of(graph, bpy.context)
    corners = [(-0.9, -0.9), (0.9, -0.9), (0.9, 0.9), (-0.9, 0.9)]
    nodes = [A.new_node(graph, np.array([x, y, 0.0]), surf)
             for x, y in corners]
    for k in range(4):
        a, b = nodes[k], nodes[(k + 1) % 4]
        pa = np.array(corners[k] + (0.0,))
        pb = np.array(corners[(k + 1) % 4] + (0.0,))
        seg = np.stack([pa + (pb - pa) * t
                        for t in np.linspace(0, 1, 12)])
        A.add_arc(graph, a, b, np.asarray(surf.project(seg), dtype=float),
                  surf)
    set_graph(obj, graph)
    refresh(obj, graph, bpy.context)
    graph = get_graph(obj)
    pid = next(iter(graph.patches))

    def interior_dev():
        w = np.array([tuple(v.co) for v in obj.data.vertices])
        inner = w[(np.abs(w[:, 0]) < 0.7) & (np.abs(w[:, 1]) < 0.7)]
        if not len(inner):
            return 0.0
        c, nrm, _r = fit_plane(inner)
        return float(np.abs((inner - c) @ nrm).max())

    dev_before = interior_dev()
    graph.set_flat(pid, True)
    set_graph(obj, graph)
    refresh(obj, graph, bpy.context)
    dev_after = interior_dev()
    out.append(("a flattened patch's interior lies on one plane",
                dev_after < 1e-6 and dev_before > 0.005,
                f"{dev_before:.4f} -> {dev_after:.2e}"))

    return out
