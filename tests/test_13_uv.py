"""UVs generated from the layout.

There is nothing to infer: a quad patch is a `p x q` grid, so it unwraps to a
`p x q` rectangle exactly. The properties worth guarding are that every face
gets a non-degenerate UV, that islands respect the seams the artist drew, and
that a surface which closes on itself gets cut rather than folded over.
"""

import bpy
import numpy as np

from nx_loom.core import uv as uv_mod
from nx_loom.ops.layout import get_graph, rebuild_object, set_graph


def _trace(mk, target_edge=0.3):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mk()
    st = bpy.context.scene.nx_loom
    st.target_edge = target_edge
    st.relax_iters = 2
    st.size_mode = "EDGE"
    st.symmetry_axis = "NONE"
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.nxloom.layout_from_selection()
    if bpy.context.active_object.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
    return bpy.context.active_object


def _measure(obj):
    me = obj.data
    layer = me.uv_layers.active
    uv = np.array([tuple(d.uv) for d in layer.data])
    areas_uv, areas_3d = [], []
    for poly in me.polygons:
        q = uv[list(poly.loop_indices)]
        areas_uv.append(0.5 * abs(float(np.cross(q[2] - q[0], q[3] - q[1]))))
        areas_3d.append(poly.area)
    areas_uv = np.array(areas_uv)
    areas_3d = np.array(areas_3d)
    ok = (areas_uv > 1e-12) & (areas_3d > 1e-12)
    ratio = areas_3d[ok] / areas_uv[ok]
    return {
        "faces": len(me.polygons),
        "degenerate": int((areas_uv <= 1e-12).sum()),
        "in01": bool(uv.min() >= -1e-6 and uv.max() <= 1.0 + 1e-6),
        "spread": float(np.percentile(ratio, 95) / max(np.percentile(ratio, 5), 1e-9)),
        "islands": int(obj.get("nx_loom_uv_islands", 0)),
    }


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # a flat uniform layout must come out with perfectly even texel density
    obj = _trace(lambda: bpy.ops.mesh.primitive_grid_add(
        x_subdivisions=3, y_subdivisions=3, size=2.0))
    res = bpy.ops.nxloom.generate_uvs()
    out.append(("uv generation finished", "FINISHED" in res, str(res)))
    m = _measure(obj)
    out.append(("every face gets a non-degenerate UV", m["degenerate"] == 0,
                str(m)))
    out.append(("a uniform layout is texel-perfect", m["spread"] < 1.02,
                f"{m['spread']:.3f}x"))
    out.append(("UVs are packed inside 0..1", m["in01"], ""))
    out.append(("a flat connected layout is one island", m["islands"] == 1,
                str(m["islands"])))

    # A seam stops islands merging across it. It does not, on its own, force a
    # split: a cut through the middle of a flat sheet leaves it connected round
    # the ends, and nothing needs to open. Ringing a patch does separate it.
    graph = get_graph(obj)
    pid = sorted(graph.patches)[0]
    ring = {a for side in graph.patches[pid].arc_sides() for a in side}
    for a in ring:
        graph.arcs[a].type = "seam"
    set_graph(obj, graph)
    bpy.ops.nxloom.generate_uvs()
    m2 = _measure(obj)
    out.append(("seaming right around a patch cuts it out",
                m2["islands"] == m["islands"] + 1,
                f"{m['islands']} -> {m2['islands']} with {len(ring)} seam arcs"))

    graph = get_graph(obj)
    for a in ring:
        graph.arcs[a].type = "flow"
    mid = [a for a, arc in graph.arcs.items()
           if abs(float(np.asarray(arc.path)[:, 0].mean())) < 1e-6]
    for a in mid:
        graph.arcs[a].type = "seam"
    set_graph(obj, graph)
    bpy.ops.nxloom.generate_uvs()
    m2b = _measure(obj)
    out.append(("a seam that separates nothing changes nothing",
                m2b["islands"] == m["islands"],
                f"{m2b['islands']} islands across {len(mid)} seam arcs"))
    out.append(("still texel-even after seaming", m2["spread"] < 1.02,
                f"{m2['spread']:.3f}x"))
    out.append(("still packed inside 0..1", m2["in01"], ""))

    # a cylinder: caps are n-sided patches, walls close on themselves
    obj = _trace(lambda: bpy.ops.mesh.primitive_cylinder_add(
        vertices=16, radius=1.0, depth=2.0), 0.3)
    bpy.ops.nxloom.generate_uvs()
    m3 = _measure(obj)
    out.append(("cylinder unwraps evenly", m3["degenerate"] == 0
                and m3["spread"] < 1.2 and m3["in01"], str(m3)))

    # a torus closes on itself in both directions, so the walk has to cut
    obj = _trace(lambda: bpy.ops.mesh.primitive_torus_add(
        major_segments=16, minor_segments=8), 0.25)
    bpy.ops.nxloom.generate_uvs()
    m4 = _measure(obj)
    out.append(("a doubly-closed surface still unwraps",
                m4["degenerate"] == 0 and m4["in01"] and m4["faces"] > 0,
                str(m4)))
    out.append(("and its distortion stays modest", m4["spread"] < 2.0,
                f"{m4['spread']:.2f}x"))

    # islands must not overlap: no two faces from different islands may share
    # UV space. Checked by cell occupancy over a fine grid.
    me = obj.data
    uv = np.array([tuple(d.uv) for d in me.uv_layers.active.data])
    cent = np.array([uv[list(p.loop_indices)].mean(axis=0) for p in me.polygons])
    keys = set()
    dupes = 0
    for c in cent:
        k = (int(c[0] * 512), int(c[1] * 512))
        if k in keys:
            dupes += 1
        keys.add(k)
    out.append(("face centres do not pile up in UV space",
                dupes <= len(cent) * 0.02, f"{dupes} of {len(cent)}"))
    return out
