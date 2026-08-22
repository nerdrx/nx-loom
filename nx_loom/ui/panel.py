"""Sidebar UI (SPEC §8). The generated mesh is a build product, and the panel
says so — layout health is reported per patch rather than left for the artist
to discover as a hole."""

import bpy

from ..core.graph import GRAPH_KEY
from ..ops.layout import active_object, get_graph


class NXLOOM_PT_main(bpy.types.Panel):
    bl_label = "NX Loom"
    bl_idname = "NXLOOM_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "NX Loom"

    def draw(self, context):
        layout = self.layout
        st = context.scene.nx_loom
        obj = active_object(context)

        col = layout.column(align=True)
        col.prop(st, "reference")

        row = layout.row(align=True)
        row.prop(st, "show_overlay", text="", icon="OVERLAY")
        row.prop(st, "overlay_xray", text="", icon="XRAY")
        row.prop(st, "arc_type", text="")

        if obj and obj.mode == "EDIT":
            box = layout.box()
            box.label(text="Author", icon="EDGESEL")
            box.operator("nxloom.layout_from_selection", icon="MESH_GRID")
            box.prop(st, "corner_angle")
            return

        has_graph = obj is not None and GRAPH_KEY in obj
        if not has_graph:
            box = layout.box()
            box.label(text="No layout on this object", icon="INFO")
            box.operator("nxloom.new_layout", icon="ADD")
            box.label(text="Then pick the Loom Draw tool")
            box.label(text="in the toolbar and draw.")
            box.separator()
            box.label(text="Or trace an existing mesh: select")
            box.label(text="edges in Edit Mode and use")
            box.label(text="Layout from Selected Edges.")
            return

        box = layout.box()
        box.label(text="Draw", icon="GREASEPENCIL")
        box.operator("nxloom.draw_arc", icon="IPO_LINEAR")
        sub = box.column(align=True)
        sub.prop(st, "snap_pixels")
        sub.prop(st, "rebuild_on_draw")

        box = layout.box()
        box.label(text="Density", icon="MOD_MESHDEFORM")
        box.prop(st, "target_edge")
        box.prop(st, "relax_iters")
        box.prop(st, "reproject")

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("nxloom.rebuild", icon="FILE_REFRESH")

        box = layout.box()
        box.label(text="Hand Edits", icon="MOD_DATA_TRANSFER")
        from ..core.delta import DELTA_KEY, count as delta_count, load as load_deltas
        n_delta = delta_count(load_deltas(obj)) if DELTA_KEY in obj else 0
        if n_delta:
            box.label(text=f"{n_delta} vertex edit(s) kept", icon="CHECKMARK")
        else:
            box.label(text="Move vertices, then capture.")
        sub = box.row(align=True)
        sub.operator("nxloom.capture_edits", icon="IMPORT")
        sub.operator("nxloom.clear_edits", text="", icon="X")

        layout.operator("nxloom.apply", icon="CHECKMARK")

        graph = get_graph(obj)
        if graph is not None:
            box = layout.box()
            box.label(text="Layout", icon="OUTLINER_DATA_SURFACE")
            grid = box.grid_flow(columns=2, even_columns=True, align=True)
            grid.label(text="Nodes")
            grid.label(text=str(len(graph.nodes)))
            grid.label(text="Arcs")
            grid.label(text=str(len(graph.arcs)))
            grid.label(text="Patches")
            grid.label(text=str(len(graph.patches)))
            sides = {}
            for p in graph.patches.values():
                sides[len(p.sides)] = sides.get(len(p.sides), 0) + 1
            for n in sorted(sides):
                grid.label(text=f"  {n}-sided")
                grid.label(text=str(sides[n]))

            bad = list(obj.get("nx_loom_bad_patches", []) or [])
            if bad:
                warn = box.column(align=True)
                warn.alert = True
                warn.label(text=f"{len(bad)} patch(es) unresolved", icon="ERROR")
                warn.label(text="Add an arc, or change density.")
            elif graph.patches:
                box.label(text="All patches resolved", icon="CHECKMARK")


_CLASSES = (NXLOOM_PT_main,)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
