"""Closed layouts on real geometry. Every bug this file guards against was
found by running these three primitives, not by reasoning about the code."""

import bmesh
import bpy

from nx_loom.ops.layout import get_graph


def _author(make, target_edge):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    make()
    src = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    st.target_edge = target_edge
    st.relax_iters = 8
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    res = bpy.ops.nxloom.layout_from_selection()
    if bpy.context.active_object.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
    return (src, bpy.context.active_object) if "FINISHED" in res else (src, None)


def _survey(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    out = {
        "V": len(bm.verts), "E": len(bm.edges), "F": len(bm.faces),
        "nonquad": sum(1 for f in bm.faces if len(f.verts) != 4),
        "nm": sum(1 for e in bm.edges if len(e.link_faces) > 2),
        "boundary": sum(1 for e in bm.edges if len(e.link_faces) == 1),
        "loose": sum(1 for v in bm.verts if not v.link_faces),
    }
    out["euler"] = out["V"] - out["E"] + out["F"]
    bm.free()
    return out


def _closed_sphere(label, make, densities, expect_sides=None):
    results = []
    for te in densities:
        src, obj = _author(make, te)
        if obj is None:
            results.append((f"{label} @ {te}", False, "operator cancelled"))
            continue
        graph = get_graph(obj)
        if expect_sides is not None:
            got = {}
            for p in graph.patches.values():
                got[len(p.sides)] = got.get(len(p.sides), 0) + 1
            results.append((f"{label} @ {te}: layout", got == expect_sides, str(got)))
        st = _survey(obj)
        ok = (st["euler"] == 2 and st["nonquad"] == 0 and st["nm"] == 0
              and st["boundary"] == 0 and st["loose"] == 0 and st["F"] > 0)
        results.append((f"{label} @ {te}: closed all-quad", ok, str(st)))
    return results


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass

    out = []

    # Every patch is a triangle and every arc is shared by two of them, so the
    # parity constraints are fully coupled. Greedy repair stalls here; the GF(2)
    # pass is what makes it solvable.
    out += _closed_sphere(
        "icosphere",
        lambda: bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1.0),
        (0.9, 0.45, 0.22, 0.11),
    )

    # Pole fans: single-arc sides of triangle patches. Without a per-arc floor
    # of 2 the GF(2) pass drags them back to 1 and the caps come out unfilled.
    out += _closed_sphere(
        "uv sphere",
        lambda: bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8, radius=1.0),
        (0.6, 0.3, 0.15),
        expect_sides={3: 24, 4: 72},
    )

    # Sharp rims: the BVH normal at a rim node is whichever facet the nearest
    # query hit, so a normal-based rotation system scrambles the traversal and
    # fuses the 16 wall quads into two giant patches. The PCA plane of the arc
    # star does not have that ambiguity.
    out += _closed_sphere(
        "cylinder",
        lambda: bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=1.0, depth=2.0),
        (0.5, 0.25),
        expect_sides={4: 16, 16: 2},
    )
    return out
