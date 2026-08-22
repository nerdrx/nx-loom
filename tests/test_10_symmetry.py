"""Symmetry, done at the layout rather than at the mesh.

Both halves share the nodes on the plane, so the seam is welded by
construction — there is no mirror-weld pass and no doubles to merge. What has
to be proven is that the result is *exactly* symmetric, because "nearly" is
what you notice on a face.
"""

import bmesh
import bpy
import numpy as np

from nx_loom.core import symmetry as sym
from nx_loom.core.surface import Surface
from nx_loom.ops.draw import commit_arc
from nx_loom.ops.layout import get_graph, rebuild_object, set_graph

N, S = (0, 0, 1), (0, 0, -1)
PX, NX_, FY, BY = (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)


def _gc(a, b, n=12, R=1.0):
    a = np.array(a, float) / np.linalg.norm(a)
    b = np.array(b, float) / np.linalg.norm(b)
    om = np.arccos(np.clip(a @ b, -1, 1))
    return [(np.sin((1 - t) * om) * a + np.sin(t * om) * b) / np.sin(om) * R
            for t in [k / n for k in range(n + 1)]]


def _rays(pts):
    return [(np.array(p) * 3.0, -np.array(p)) for p in pts]


def _setup(axis="X", target_edge=0.3):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    src = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    st.target_edge = target_edge
    st.relax_iters = 6
    st.symmetry_axis = axis
    st.symmetry_tolerance = 0.02
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object
    return src, obj, Surface(src, bpy.context.evaluated_depsgraph_get())


def _draw_half(graph, surf, axis="X"):
    """A ring in the mirror plane, plus spokes out to one pole of the axis.

    The ring has to lie *in* the plane being mirrored across, otherwise the
    layout is not a half of anything and patches straddle the seam.
    """
    if axis == "X":
        ring, spoke = (N, FY, S, BY), PX
    else:
        ring, spoke = (N, PX, S, NX_), FY
    pairs = [(ring[i], ring[(i + 1) % 4]) for i in range(4)]
    pairs += [(ring[0], spoke), (spoke, ring[2]),
              (ring[1], spoke), (spoke, ring[3])]
    for a, b in pairs:
        commit_arc(graph, surf, _rays(_gc(a, b)), 0.08, 0.02)


def _mirror_err(obj, ax=0):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    P = np.array([tuple(obj.matrix_world @ v.co) for v in bm.verts])
    info = dict(V=len(bm.verts), F=len(bm.faces),
                nm=sum(1 for e in bm.edges if len(e.link_faces) > 2),
                bnd=sum(1 for e in bm.edges if len(e.link_faces) == 1),
                nonquad=sum(1 for f in bm.faces if len(f.verts) != 4))
    bm.free()
    if not len(P):
        return 9e9, info
    M = P.copy()
    M[:, ax] *= -1
    d = np.linalg.norm(P[:, None, :] - M[None, :, :], axis=2).min(axis=1)
    return float(d.max()), info


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    src, obj, surf = _setup()
    graph = get_graph(obj)
    _draw_half(graph, surf, "X")
    authored_arcs, authored_nodes = len(graph.arcs), len(graph.nodes)

    rep = sym.sync(graph, "X", 0.02, surf)
    out.append(("mirroring only touches off-plane arcs",
                rep["mirrored"] == 4 and rep["on_plane"] == 4, str(rep)))
    out.append(("on-plane nodes are shared, not duplicated",
                len(graph.nodes) == authored_nodes + 1,
                f"{authored_nodes} -> {len(graph.nodes)}"))
    out.append(("mirrored arcs are marked derived",
                sum(1 for a in graph.arcs.values() if a.mirror_of is not None) == 4,
                ""))

    set_graph(obj, graph)
    rebuild_object(obj, bpy.context)
    err, info = _mirror_err(obj)
    out.append(("the generated mesh is exactly symmetric", err == 0.0,
                f"max mirror error {err:.2e}"))
    out.append(("and closed, all-quad, welded at the seam",
                info["nm"] == 0 and info["bnd"] == 0 and info["nonquad"] == 0
                and info["V"] - (info["V"] + info["F"] - 2) == 2 - info["F"],
                str(info)))

    # exactness must hold at every density, not just the one it was built at
    errs = []
    for te in (0.5, 0.2, 0.1):
        bpy.context.scene.nx_loom.target_edge = te
        rebuild_object(obj, bpy.context)
        e, i2 = _mirror_err(obj)
        errs.append((te, e, i2["F"], i2["nm"], i2["bnd"]))
    out.append(("exact at every density", all(e[1] == 0.0 for e in errs),
                str([(e[0], f"{e[1]:.0e}", e[2]) for e in errs])))
    out.append(("still closed at every density",
                all(e[3] == 0 and e[4] == 0 for e in errs), str(errs)))

    g2 = get_graph(obj)
    mism = [a for a in g2.arcs
            if g2.arcs[a].mirror_of is not None
            and g2.arcs[a].n != g2.arcs[g2.arcs[a].mirror_of].n]
    out.append(("mirrored arcs get identical subdivision counts", not mism,
                str(mism)))

    # deleting an authored arc must take its mirror with it
    bpy.context.scene.nx_loom.target_edge = 0.3
    g3 = get_graph(obj)
    victim = next(a for a, arc in g3.arcs.items() if arc.mirror_of is None
                  and abs(np.asarray(arc.path)[:, 0]).max() > 0.5)
    del g3.arcs[victim]
    before = len(g3.arcs)
    r = sym.sync(g3, "X", 0.02, surf)
    out.append(("deleting an authored arc drops its mirror too",
                len(g3.arcs) == before - 1 or r["mirrored"] == 3,
                f"{before} -> {len(g3.arcs)}, mirrored {r['mirrored']}"))

    # turning symmetry off leaves only what was authored
    g4 = get_graph(obj)
    sym.sync(g4, "NONE", 0.02, surf)
    out.append(("symmetry off leaves only authored arcs",
                all(a.mirror_of is None for a in g4.arcs.values())
                and len(g4.arcs) == authored_arcs,
                f"{len(g4.arcs)} vs {authored_arcs} authored"))

    # an arc drawn across the centre line is cut at the plane
    src, obj2, surf2 = _setup()
    g5 = get_graph(obj2)
    commit_arc(g5, surf2, _rays(_gc(PX, NX_, n=16)), 0.08, 0.02)
    n_before = len(g5.arcs)
    r5 = sym.sync(g5, "X", 0.02, surf2)
    on_plane_nodes = sum(1 for nd in g5.nodes.values() if abs(nd.co[0]) < 1e-9)
    out.append(("an arc crossing the plane is split at it",
                r5["split"] >= 1 and len(g5.arcs) > n_before and on_plane_nodes >= 1,
                f"{n_before} -> {len(g5.arcs)} arcs, split {r5['split']}, "
                f"{on_plane_nodes} on-plane node(s)"))

    # Y axis works too
    src, obj3, surf3 = _setup(axis="Y")
    g6 = get_graph(obj3)
    _draw_half(g6, surf3, "Y")
    sym.sync(g6, "Y", 0.02, surf3)
    set_graph(obj3, g6)
    rebuild_object(obj3, bpy.context)
    ey, iy = _mirror_err(obj3, ax=1)
    out.append(("Y symmetry is exact too", ey == 0.0 and iy["nm"] == 0,
                f"{ey:.2e}, {iy}"))

    bpy.context.scene.nx_loom.symmetry_axis = "X"
    out += run_mirror_edits()
    return out


def run_mirror_edits():
    """The Mirror Hand Edits toggle."""
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    from nx_loom.core import delta as delta_mod
    from nx_loom.ops.layout import DELTA_KEY, clean_build

    def build_sym(mirror_edits):
        src, obj, surf = _setup()
        g = get_graph(obj)
        _draw_half(g, surf, "X")
        set_graph(obj, g)
        st = bpy.context.scene.nx_loom
        st.mirror_edits = mirror_edits
        rebuild_object(obj, bpy.context)
        return obj

    # capture with symmetry on must not record the symmetrisation itself
    obj = build_sym(False)
    clean, prov, _ = clean_build(obj, bpy.context)
    world = np.array([tuple(obj.matrix_world @ v.co) for v in obj.data.vertices])
    out.append(("clean_build matches the symmetrised rebuild",
                clean.shape == world.shape
                and float(np.abs(clean - world).max()) < 1e-5,
                f"max {float(np.abs(clean - world).max()):.1e}"
                if clean.shape == world.shape else "shape mismatch"))
    bpy.ops.nxloom.capture_edits()
    out.append(("an untouched symmetric mesh captures nothing",
                delta_mod.count(delta_mod.load(obj)) == 0,
                str(delta_mod.count(delta_mod.load(obj)))))

    # off: the edit stays where it was made
    obj = build_sym(False)
    mw, mwi = obj.matrix_world, obj.matrix_world.inverted()
    picked = [i for i, v in enumerate(obj.data.vertices) if (mw @ v.co).x > 0.35][:3]
    for i in picked:
        co = mw @ obj.data.vertices[i].co
        obj.data.vertices[i].co = mwi @ (co * 1.25)
    bpy.ops.nxloom.capture_edits()
    n_off = delta_mod.count(delta_mod.load(obj))
    rebuild_object(obj, bpy.context)
    err_off, _ = _mirror_err(obj)
    out.append(("off: only the edited side moves",
                n_off == len(picked) and err_off > 1e-3,
                f"{n_off} deltas, mirror error {err_off:.3e}"))

    # on: the edit appears on both halves and the mesh stays exact
    obj = build_sym(True)
    mw, mwi = obj.matrix_world, obj.matrix_world.inverted()
    picked = [i for i, v in enumerate(obj.data.vertices) if (mw @ v.co).x > 0.35][:3]
    for i in picked:
        co = mw @ obj.data.vertices[i].co
        obj.data.vertices[i].co = mwi @ (co * 1.25)
    bpy.ops.nxloom.capture_edits()
    n_on = delta_mod.count(delta_mod.load(obj))
    rebuild_object(obj, bpy.context)
    err_on, info = _mirror_err(obj)
    out.append(("on: each edit is stored for both halves",
                n_on == 2 * len(picked), f"{n_on} deltas from {len(picked)} edits"))
    out.append(("on: the mesh stays exactly symmetric", err_on == 0.0,
                f"mirror error {err_on:.3e}"))
    out.append(("on: and stays clean",
                info["nm"] == 0 and info["nonquad"] == 0 and info["bnd"] == 0,
                str(info)))

    # a seam vertex cannot be pushed off the plane, or symmetry breaks
    obj = build_sym(True)
    mw, mwi = obj.matrix_world, obj.matrix_world.inverted()
    seam = [i for i, v in enumerate(obj.data.vertices)
            if abs((mw @ v.co).x) < 1e-6]
    if seam:
        i = seam[0]
        co = mw @ obj.data.vertices[i].co
        obj.data.vertices[i].co = mwi @ (co + __import__("mathutils").Vector((0.3, 0, 0.1)))
        bpy.ops.nxloom.capture_edits()
        rebuild_object(obj, bpy.context)
        P = np.array([tuple(obj.matrix_world @ v.co) for v in obj.data.vertices])
        still_seam = int((np.abs(P[:, 0]) < 1e-6).sum())
        e2, _ = _mirror_err(obj)
        out.append(("a seam edit keeps the seam on the plane",
                    still_seam == len(seam) and e2 == 0.0,
                    f"{len(seam)} -> {still_seam} seam verts, err {e2:.1e}"))
    return out
