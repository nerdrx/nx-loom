"""Toolbar tools.

Drawing lives in the toolbar rather than behind a shortcut nobody finds. The
keymap is scoped to the tool, so LMB means "draw an arc" only while the Loom
tool is active and every other Blender binding is left alone.
"""

import bpy


class NXLOOM_TOOL_draw(bpy.types.WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = "nxloom.draw_tool"
    bl_label = "Loom Draw"
    bl_description = (
        "Draw layout arcs on the reference surface.\n"
        "Click to chain straight segments, drag to draw freehand.\n"
        "Ctrl: erase arc / dissolve node     Shift: drag a node "
        "(drop it on another node to merge them)\n"
        "Ctrl+Shift: toggle a patch between filled and a hole\n"
        "Ctrl+Wheel: pin the loop count across the arc under the cursor\n"
        "Ctrl+Alt+Wheel: more or less resolution inside one patch\n"
        "Alt+Shift: select an arc, then type its loop count in the sidebar\n"
        "Ctrl+Alt: swipe across a limb to ring it in one stroke\n"
        "Ctrl+Alt+Shift: drag outward from a point to halo it (eyes, mouths)\n"
        "C: loop cut through the quad strip under the cursor     1-4: arc type\n"
        "R: repeat the last ring at the same spacing"
    )
    bl_icon = "ops.mesh.knife_tool"
    bl_widget = None
    bl_keymap = (
        ("nxloom.hover", {"type": "MOUSEMOVE", "value": "ANY"}, None),
        ("nxloom.draw_arc", {"type": "LEFTMOUSE", "value": "PRESS"}, None),
        ("nxloom.erase", {"type": "LEFTMOUSE", "value": "PRESS", "ctrl": True}, None),
        ("nxloom.ring_cut",
         {"type": "LEFTMOUSE", "value": "PRESS", "ctrl": True, "alt": True}, None),
        ("nxloom.halo",
         {"type": "LEFTMOUSE", "value": "PRESS",
          "ctrl": True, "alt": True, "shift": True}, None),
        ("nxloom.loop_cut", {"type": "C", "value": "PRESS"}, None),
        ("nxloom.repeat_ring", {"type": "R", "value": "PRESS"}, None),
        ("nxloom.toggle_freeze", {"type": "F", "value": "PRESS"}, None),
        ("nxloom.brush", {"type": "B", "value": "PRESS"}, None),
        ("nxloom.set_arc_type_key", {"type": "ONE", "value": "PRESS"},
         {"properties": [("kind", "flow")]}),
        ("nxloom.set_arc_type_key", {"type": "TWO", "value": "PRESS"},
         {"properties": [("kind", "crease")]}),
        ("nxloom.set_arc_type_key", {"type": "THREE", "value": "PRESS"},
         {"properties": [("kind", "boundary")]}),
        ("nxloom.set_arc_type_key", {"type": "FOUR", "value": "PRESS"},
         {"properties": [("kind", "seam")]}),
        ("nxloom.move_node", {"type": "LEFTMOUSE", "value": "PRESS", "shift": True}, None),
        ("nxloom.set_arc_type",
         {"type": "LEFTMOUSE", "value": "PRESS", "alt": True}, None),
        ("nxloom.toggle_hole",
         {"type": "LEFTMOUSE", "value": "PRESS", "ctrl": True, "shift": True}, None),
        ("nxloom.select_arc",
         {"type": "LEFTMOUSE", "value": "PRESS", "alt": True, "shift": True},
         None),
        ("nxloom.adjust_loops",
         {"type": "WHEELUPMOUSE", "value": "PRESS", "ctrl": True},
         {"properties": [("delta", 1)]}),
        ("nxloom.adjust_loops",
         {"type": "WHEELDOWNMOUSE", "value": "PRESS", "ctrl": True},
         {"properties": [("delta", -1)]}),
        ("nxloom.adjust_patch_density",
         {"type": "WHEELUPMOUSE", "value": "PRESS", "ctrl": True, "alt": True},
         {"properties": [("factor", 1.25)]}),
        ("nxloom.adjust_patch_density",
         {"type": "WHEELDOWNMOUSE", "value": "PRESS", "ctrl": True, "alt": True},
         {"properties": [("factor", 0.8)]}),
    )

    def draw_settings(context, layout, tool):
        st = context.scene.nx_loom
        row = layout.row(align=True)
        row.prop(st, "arc_type", text="")
        row.prop(st, "snap_pixels")
        row.prop(st, "bridge_rings", text="", icon="MOD_LATTICE")
        row.prop(st, "rebuild_on_draw", text="", icon="FILE_REFRESH")
        row.prop(st, "overlay_xray", text="", icon="XRAY")


_TOOLS = (NXLOOM_TOOL_draw,)


def register():
    for t in _TOOLS:
        bpy.utils.register_tool(t, after={"builtin.transform"}, separator=True)


def unregister():
    for t in reversed(_TOOLS):
        try:
            bpy.utils.unregister_tool(t)
        except Exception:
            pass
