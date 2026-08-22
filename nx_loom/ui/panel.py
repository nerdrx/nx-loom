"""Sidebar UI (SPEC §8).

Split into collapsible sub-panels: the layout is a long-lived document and the
panel is looked at constantly, so everything that is not being adjusted right
now should be foldable out of the way.

Nothing here invokes an operator that needs a viewport mouse position. A button
in the sidebar is pressed with the cursor over the sidebar, so an operator that
raycasts from `event.mouse_region_x/y` picks nothing — those live in the tool
keymap and the panel only says so.
"""

import bpy

_FLOOR_CACHE = {}

from ..core.delta import DELTA_KEY, count as delta_count, load as load_deltas
from ..core.graph import GRAPH_KEY
from ..ops.layout import active_object, get_graph


def _has_layout(context):
    obj = active_object(context)
    return obj is not None and GRAPH_KEY in obj


def _reference_of(context, graph):
    """The reference the layout actually uses, not the scene fallback."""
    if graph is not None and graph.reference:
        ref = bpy.data.objects.get(graph.reference)
        if ref is not None:
            return ref, True
    return context.scene.nx_loom.reference, False


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

        if obj and obj.mode == "EDIT":
            box = layout.box()
            box.label(text="Trace an existing mesh", icon="EDGESEL")
            box.operator("nxloom.layout_from_selection", icon="MESH_GRID")
            box.prop(st, "corner_angle")
            return

        if not _has_layout(context):
            col = layout.column(align=True)
            col.prop(st, "reference")
            box = layout.box()
            box.label(text="No layout on this object", icon="INFO")
            box.operator("nxloom.new_layout", icon="ADD")
            box.separator()
            box.label(text="Then pick Loom Draw in the")
            box.label(text="toolbar (or press the button")
            box.label(text="below) and draw on the surface.")
            box.operator("nxloom.activate_draw_tool", icon="GREASEPENCIL")
            box.separator()
            box.label(text="Or trace a mesh you already")
            box.label(text="have: select edges in Edit")
            box.label(text="Mode, then Layout from")
            box.label(text="Selected Edges.")
            return

        graph = get_graph(obj)
        ref, from_graph = _reference_of(context, graph)

        row = layout.row()
        row.enabled = False
        row.label(text=f"Reference: {ref.name if ref else 'none'}"
                  + ("" if from_graph else "  (scene default)"),
                  icon="MESH_DATA")
        if ref is not None:
            layout.operator("nxloom.toggle_reference", icon="HIDE_OFF",
                            text="Show Reference" if ref.hide_get()
                            else "Hide Reference")

        layout.operator("nxloom.activate_draw_tool", icon="GREASEPENCIL")

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("nxloom.rebuild", icon="FILE_REFRESH")

        bad = list(obj.get("nx_loom_bad_patches", []) or [])
        if bad:
            warn = layout.box().column(align=True)
            warn.alert = True
            warn.label(text=f"{len(bad)} patch(es) unresolved", icon="ERROR")
            warn.label(text="Add an arc, mark it a hole,")
            warn.label(text="or change the size.")
            warn.operator("nxloom.frame_problem", icon="ZOOM_SELECTED")
        elif graph is not None and graph.patches:
            layout.label(text="All patches resolved", icon="CHECKMARK")


class _Sub:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "NX Loom"
    bl_parent_id = "NXLOOM_PT_main"

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return _has_layout(context) and obj.mode != "EDIT"


class NXLOOM_PT_size(_Sub, bpy.types.Panel):
    bl_label = "Size"

    def draw(self, context):
        layout = self.layout
        st = context.scene.nx_loom
        layout.prop(st, "size_mode", expand=True)
        if st.size_mode == "COUNT":
            layout.prop(st, "target_count")
            obj = active_object(context)
            n = len(obj.data.polygons) if obj else 0
            if n:
                off = abs(n - st.target_count) / max(st.target_count, 1) * 100.0
                layout.label(text=f"{n} faces  ({off:.0f}% off budget)",
                             icon="MESH_GRID")
            graph = get_graph(obj)
            if graph is not None and graph.patches:
                # floor_faces runs the whole quantiser; a panel redraws on
                # every mouse move over it, so the result is cached against
                # the stored blob rather than recomputed per draw.
                from ..core.graph import GRAPH_KEY as _GK

                from ..core.build import floor_faces
                blob = obj.get(_GK, "")
                key = (obj.as_pointer(), len(blob), hash(blob),
                       st.fill_background)
                hit = _FLOOR_CACHE.get("f")
                if hit is not None and hit[0] == key:
                    fl = hit[1]
                else:
                    fl = floor_faces(graph, st.fill_background)
                    _FLOOR_CACHE["f"] = (key, fl)
                if st.target_count < fl:
                    col = layout.column(align=True)
                    col.alert = True
                    col.label(text=f"Layout floor is {fl} faces", icon="ERROR")
                    col.label(text="Fewer needs a coarser layout,")
                    col.label(text="not a coarser solve.")
        else:
            layout.prop(st, "target_edge")
        col = layout.column(align=True)
        col.prop(st, "relax_iters")
        col.prop(st, "reproject")
        col.prop(st, "fill_background")

        obj = active_object(context)
        graph = get_graph(obj)
        pinned = sum(1 for a in graph.arcs.values() if a.n_lock) if graph else 0

        box = layout.box()
        box.label(text="Loops", icon="MOD_ARRAY")

        from ..ops.draw import active_arc
        aid = active_arc(obj)
        arc = graph.arcs.get(aid) if (graph and aid is not None) else None
        if arc is not None:
            col = box.column(align=True)
            col.label(text=f"Arc {aid}"
                           + ("  (pinned)" if arc.n_lock else "  (solved)"),
                      icon="PINNED" if arc.n_lock else "DECORATE")
            col.prop(st, "active_loops")
            row = col.row(align=True)
            row.enabled = bool(arc.n_lock)
            row.operator("nxloom.unpin_arc", icon="UNLINKED")
        else:
            box.label(text="Alt+Shift click an arc to")
            box.label(text="select it, then type its")
            box.label(text="loop count here.")

        box.separator()
        box.label(text="Or Ctrl+Wheel over an arc.")
        if pinned:
            row = box.row()
            row.label(text=f"{pinned} arc(s) pinned", icon="PINNED")
            row.operator("nxloom.clear_loop_locks", text="", icon="X")
        n_conf = int(obj.get("nx_loom_lock_conflicts", 0) or 0)
        if n_conf:
            col = box.column(align=True)
            col.alert = True
            col.label(text=f"{n_conf} pin(s) conflict", icon="ERROR")
            col.label(text="Mirrored halves pinned to")
            col.label(text="different counts.")

        box = layout.box()
        box.label(text="Ctrl+Alt+Wheel over a patch")
        box.label(text="for more or less detail there.")
        n_dens = len(graph.settings.get("density", {})) if graph else 0
        if n_dens:
            row = box.row()
            row.label(text=f"{n_dens} patch override(s)", icon="MOD_MESHDEFORM")
            row.operator("nxloom.clear_patch_density", text="", icon="X")


class NXLOOM_PT_symmetry(_Sub, bpy.types.Panel):
    bl_label = "Symmetry"

    def draw_header(self, context):
        st = context.scene.nx_loom
        self.layout.label(text="", icon="MOD_MIRROR"
                          if st.symmetry_axis != "NONE" else "BLANK1")

    def draw(self, context):
        layout = self.layout
        st = context.scene.nx_loom
        layout.prop(st, "symmetry_axis", expand=True)
        if st.symmetry_axis == "NONE":
            return
        layout.prop(st, "symmetry_tolerance")
        graph = get_graph(active_object(context))
        if graph is not None:
            n_mir = sum(1 for a in graph.arcs.values() if a.mirror_of is not None)
            n_twin = sum(1 for a in graph.arcs.values() if a.twin is not None)
            layout.label(text=f"{n_mir} mirrored, {n_twin} paired",
                         icon="CHECKMARK")


class NXLOOM_PT_edits(_Sub, bpy.types.Panel):
    bl_label = "Hand Edits"

    def draw(self, context):
        layout = self.layout
        st = context.scene.nx_loom
        obj = active_object(context)
        n = delta_count(load_deltas(obj)) if DELTA_KEY in obj else 0
        if n:
            layout.label(text=f"{n} vertex edit(s) kept", icon="CHECKMARK")
        else:
            layout.label(text="Move vertices in Edit Mode,")
            layout.label(text="then capture them here.")
        row = layout.row()
        row.enabled = st.symmetry_axis != "NONE"
        row.prop(st, "mirror_edits")
        row = layout.row(align=True)
        row.operator("nxloom.capture_edits", icon="IMPORT")
        row.operator("nxloom.clear_edits", text="", icon="X")


class NXLOOM_PT_finish(_Sub, bpy.types.Panel):
    bl_label = "Finish"

    def draw(self, context):
        layout = self.layout
        st = context.scene.nx_loom
        layout.prop(st, "transfer_data")
        layout.label(text="Apply drops the layout.", icon="ERROR")
        layout.operator("nxloom.apply", icon="CHECKMARK")


class NXLOOM_PT_retarget(_Sub, bpy.types.Panel):
    bl_label = "Retarget"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        st = context.scene.nx_loom
        layout.label(text="Move this layout onto")
        layout.label(text="another mesh, topology and")
        layout.label(text="all. Landmarks come from")
        layout.label(text="shared bone names if both")
        layout.label(text="meshes are rigged.")
        layout.prop(st, "retarget_to")
        layout.operator("nxloom.retarget", icon="MOD_MESHDEFORM")


class NXLOOM_PT_uv(_Sub, bpy.types.Panel):
    bl_label = "UVs"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.label(text="Each patch is already a grid,")
        layout.label(text="so there is nothing to infer.")
        layout.label(text="Arcs typed Seam cut islands.")
        layout.operator("nxloom.generate_uvs", icon="UV")


class NXLOOM_PT_lods(_Sub, bpy.types.Panel):
    bl_label = "LODs"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.label(text="Every level shares this")
        layout.label(text="layout's structure, so UVs")
        layout.label(text="and seams match across them.")
        layout.operator("nxloom.make_lods", icon="MOD_DECIM")


class NXLOOM_PT_display(_Sub, bpy.types.Panel):
    bl_label = "Display"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        st = context.scene.nx_loom
        layout.prop(st, "show_overlay")
        layout.prop(st, "overlay_xray")
        layout.prop(st, "show_counts")
        layout.prop(st, "corner_angle")
        layout.prop(st, "snap_pixels")
        layout.prop(st, "pick_pixels")
        layout.prop(st, "node_falloff")
        layout.prop(st, "bridge_rings")
        layout.prop(st, "rebuild_on_draw")


class NXLOOM_PT_stats(_Sub, bpy.types.Panel):
    bl_label = "Layout"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        obj = active_object(context)
        graph = get_graph(obj)
        if graph is None:
            return
        grid = layout.grid_flow(columns=2, even_columns=True, align=True)
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
        n_holes = sum(1 for p in graph.patches.values() if p.fill == "hole")
        if n_holes:
            grid.label(text="  holes")
            grid.label(text=str(n_holes))
        n_bg = len(obj.get("nx_loom_background", []) or [])
        if n_bg:
            grid.label(text="  background")
            grid.label(text=str(n_bg))


_CLASSES = (NXLOOM_PT_main, NXLOOM_PT_size, NXLOOM_PT_symmetry,
            NXLOOM_PT_edits, NXLOOM_PT_retarget, NXLOOM_PT_uv,
            NXLOOM_PT_lods,
            NXLOOM_PT_finish,
            NXLOOM_PT_display, NXLOOM_PT_stats)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
