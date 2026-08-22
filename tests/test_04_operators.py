"""End to end inside Blender: select edges, author a layout, rebuild, apply."""

import bmesh
import bpy

from nx_loom.core.graph import GRAPH_KEY
from nx_loom.ops.layout import get_graph


def _fresh_grid(subdiv=2, size=2.0):
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=subdiv, y_subdivisions=subdiv, size=size)
    return bpy.context.active_object


def run():
    out = []
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass

    bpy.ops.wm.read_factory_settings(use_empty=True)
    src = _fresh_grid(subdiv=2)
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.5
    st.relax_iters = 5

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    res = bpy.ops.nxloom.layout_from_selection()
    out.append(("operator finished", "FINISHED" in res, str(res)))
    if bpy.context.active_object.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")

    obj = bpy.context.active_object
    out.append(("generated a new object", obj is not None and obj.name.endswith("_loom"),
                obj.name if obj else "none"))
    out.append(("layout stored on the object", GRAPH_KEY in obj, ""))

    graph = get_graph(obj)
    out.append(("graph round-trips off the object", graph is not None, ""))
    if graph is None:
        return out
    out.append(("patches discovered", len(graph.patches) == 4, str(len(graph.patches))))
    out.append(("all patches are quads",
                all(len(p.sides) == 4 for p in graph.patches.values()),
                str([len(p.sides) for p in graph.patches.values()])))
    out.append(("nodes are pinned to the reference",
                all(n.pin is not None for n in graph.nodes.values()), ""))
    out.append(("reference recorded", graph.reference == src.name, graph.reference))

    n_before = len(obj.data.polygons)
    out.append(("mesh generated", n_before > 0, f"{n_before} faces"))
    out.append(("all quads", all(len(p.vertices) == 4 for p in obj.data.polygons), ""))

    # the density slider re-grids the whole model, and it still closes
    st.target_edge = 0.2
    bpy.ops.nxloom.rebuild()
    n_after = len(obj.data.polygons)
    out.append(("density slider re-grids", n_after > n_before, f"{n_before} -> {n_after}"))
    out.append(("still all quads after re-grid",
                all(len(p.vertices) == 4 for p in obj.data.polygons), ""))

    # no loose or doubled verts: patches are welded, not merged
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    loose = sum(1 for v in bm.verts if not v.link_faces)
    nonmanifold = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    bm.free()
    out.append(("no loose verts", loose == 0, str(loose)))
    out.append(("no non-manifold edges", nonmanifold == 0, str(nonmanifold)))

    # rebuilding twice is stable
    v1 = len(obj.data.vertices)
    bpy.ops.nxloom.rebuild()
    out.append(("rebuild is idempotent", len(obj.data.vertices) == v1,
                f"{v1} -> {len(obj.data.vertices)}"))

    # apply drops the layout and leaves a plain mesh
    faces = len(obj.data.polygons)
    bpy.ops.nxloom.apply()
    out.append(("apply drops the layout", GRAPH_KEY not in obj, ""))
    out.append(("apply keeps the mesh", len(obj.data.polygons) == faces, ""))
    return out
