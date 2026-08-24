"""Radial symmetry: one authored wedge becomes N ghosts.

Contracts: N-fold copies land exactly at their rotations, arrive as ghosts
(never geometry), accept commits and welds them through the standard lane,
and re-running proposes nothing — the coverage skip makes it idempotent.
"""

import bpy
import numpy as np

from nx_loom.core import authoring as A
from nx_loom.ops.layout import get_graph, set_graph
from nx_loom.ops.radial import radial_ghosts


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # ---- pure math: rotations land where they should -------------------
    line = np.stack([np.full(9, 1.0), np.zeros(9),
                     np.linspace(-0.5, 0.5, 9)], axis=1)
    ghosts = radial_ghosts([line], 4, 2, span=2.0)
    out.append(("N=4 makes three copies of one arc",
                len(ghosts) == 3, f"{len(ghosts)}"))
    want = np.stack([np.zeros(9), np.full(9, 1.0),
                     np.linspace(-0.5, 0.5, 9)], axis=1)
    hit = any(np.allclose(g, want, atol=1e-9) for g in ghosts)
    out.append(("a copy lands exactly at 90 degrees", hit, ""))

    again = radial_ghosts([line] + [np.asarray(g) for g in ghosts], 4, 2,
                          span=2.0)
    out.append(("radializing a complete set proposes nothing",
                len(again) == 0, f"{len(again)}"))

    # ---- end to end on a cylinder: wedge -> ghosts -> committed arcs ---
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1.0, depth=2.0)
    cyl = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    st.reference = cyl
    st.radial_count = 4
    st.radial_axis = "Z"
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object
    graph = get_graph(obj)

    from nx_loom.ops.draw import _surface_of
    surf = _surface_of(graph, bpy.context)
    seg = np.stack([np.full(9, 1.0), np.zeros(9),
                    np.linspace(-0.6, 0.6, 9)], axis=1)
    na = A.new_node(graph, seg[0], surf)
    nb = A.new_node(graph, seg[-1], surf)
    A.add_arc(graph, na, nb, seg, surf)
    set_graph(obj, graph)

    res = bpy.ops.nxloom.radialize()
    graph = get_graph(obj)
    ghosts = graph.settings.get("suggestions") or []
    polys_before = len(obj.data.polygons)
    out.append(("radialize proposes ghosts, not geometry",
                "FINISHED" in res and len(ghosts) == 3
                and len(obj.data.polygons) == polys_before,
                f"{len(ghosts)} ghosts"))

    arcs_before = len(graph.arcs)
    res = bpy.ops.nxloom.suggest_accept()
    graph = get_graph(obj)
    out.append(("accepting commits the copies as ordinary arcs",
                "FINISHED" in res and len(graph.arcs) >= arcs_before + 3
                and not (graph.settings.get("suggestions") or []),
                f"{len(graph.arcs) - arcs_before} new arcs"))

    res = bpy.ops.nxloom.radialize()
    graph = get_graph(obj)
    out.append(("a second radialize finds everything already there",
                "CANCELLED" in res
                and not (graph.settings.get("suggestions") or []),
                str(res)))

    return out
