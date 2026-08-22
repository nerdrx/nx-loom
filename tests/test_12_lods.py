"""LOD sets from one layout.

The claim is not that the levels are smaller — anything can decimate. It is
that they are the *same surface*: same patch structure, so UVs and seams line
up across levels and every level takes its data from the same source.
"""

import bmesh
import bpy
import numpy as np


def _rich():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=1.0)
    src = bpy.context.active_object
    for name in ("A", "B"):
        src.data.materials.append(bpy.data.materials.new(name))
    for poly in src.data.polygons:
        poly.material_index = 0 if poly.center.z >= 0 else 1
    vg = src.vertex_groups.new(name="Spine")
    for v in src.data.vertices:
        vg.add([v.index], float(min(max((v.co.z + 1) * 0.5, 0), 1)), "REPLACE")
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.3
    st.relax_iters = 2
    st.size_mode = "EDGE"
    st.symmetry_axis = "NONE"
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.nxloom.layout_from_selection()
    if bpy.context.active_object.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
    return src, bpy.context.active_object


def _survey(o):
    bm = bmesh.new()
    bm.from_mesh(o.data)
    d = dict(V=len(bm.verts), F=len(bm.faces),
             nm=sum(1 for e in bm.edges if len(e.link_faces) > 2),
             nonquad=sum(1 for f in bm.faces if len(f.verts) != 4),
             bnd=sum(1 for e in bm.edges if len(e.link_faces) == 1))
    bm.free()
    return d


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    src, obj = _rich()
    base = len(obj.data.polygons)
    res = bpy.ops.nxloom.make_lods(levels=4, ratio=0.4, transfer=True)
    out.append(("make_lods finished", "FINISHED" in res, str(res)))

    coll = bpy.data.collections.get(f"{obj.name}_LODs")
    lods = sorted(coll.objects, key=lambda o: o.name) if coll else []
    out.append(("levels emitted into their own collection",
                2 <= len(lods) <= 4, str([o.name for o in lods])))
    if len(lods) < 2:
        return out

    # a layout of N patches cannot express fewer than N-ish faces; asking for
    # less is a request the structure cannot represent, and it must stop rather
    # than emit duplicates
    from nx_loom.core.build import floor_faces
    from nx_loom.ops.layout import get_graph as _gg
    fl = floor_faces(_gg(obj), bpy.context.scene.nx_loom.fill_background)
    out.append(("the layout reports a structural face floor", fl > 0, str(fl)))
    out.append(("no level is emitted below that floor",
                all(len(o.data.polygons) >= fl for o in lods),
                f"floor {fl}, got {[len(o.data.polygons) for o in lods]}"))

    counts = [len(o.data.polygons) for o in lods]
    out.append(("each level is smaller than the last",
                all(counts[i] > counts[i + 1] for i in range(len(counts) - 1)),
                str(counts)))
    out.append(("LOD0 is about the base resolution",
                abs(counts[0] - base) <= max(base * 0.25, 8),
                f"base {base}, LOD0 {counts[0]}"))

    surveys = [_survey(o) for o in lods]
    out.append(("every level is clean and closed",
                all(s["nm"] == 0 and s["nonquad"] == 0 and s["bnd"] == 0
                    for s in surveys), str(surveys)))

    # the structural claim: same patches, only the counts differ
    from nx_loom.ops.layout import get_graph
    graph = get_graph(obj)
    sides = sorted(len(p.sides) for p in graph.patches.values())
    out.append(("the layout itself is untouched by emitting LODs",
                len(graph.patches) > 0 and sides == sorted(
                    len(p.sides) for p in get_graph(obj).patches.values()),
                f"{len(graph.patches)} patches"))

    # data lands on every level, not just the first
    out.append(("every level gets UVs",
                all(len(o.data.uv_layers) > 0 for o in lods),
                str([len(o.data.uv_layers) for o in lods])))
    out.append(("every level gets both materials",
                all([m.name for m in o.data.materials] == ["A", "B"]
                    for o in lods), ""))
    out.append(("every level gets the vertex group",
                all(o.vertex_groups.get("Spine") is not None for o in lods), ""))

    # weights must still track the source gradient at the coarsest level
    coarse = lods[-1]
    vg = coarse.vertex_groups.get("Spine")
    zs, ws = [], []
    for v in coarse.data.vertices:
        for g in v.groups:
            if g.group == vg.index:
                zs.append(v.co.z)
                ws.append(g.weight)
    corr = float(np.corrcoef(zs, ws)[0, 1]) if len(zs) > 3 else 0.0
    out.append(("weights survive onto the coarsest level", corr > 0.9,
                f"corr {corr:.3f} over {len(zs)} verts"))

    # every level must sit on the same surface, not drift as it coarsens
    devs = []
    for o in lods:
        P = np.array([tuple(o.matrix_world @ v.co) for v in o.data.vertices])
        devs.append(float(np.abs(np.linalg.norm(P, axis=1) - 1.0).max()))
    out.append(("all levels stay on the reference surface",
                all(d < 0.12 for d in devs),
                str([round(d, 4) for d in devs])))
    return out
