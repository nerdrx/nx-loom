bl_info = {
    "name": "NX Loom",
    "author": "nerdrx + Claude",
    "version": (0, 10, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > NX Loom",
    "description": "Authored topology: draw the layout, the quads are generated. "
                   "Non-destructive until you Apply",
    "category": "Mesh",
    "doc_url": "https://github.com/nerdrx/nx-loom",
}

# The bpy-dependent half is imported lazily. `nx_loom.core.quantize` and
# `nx_loom.core.fill` must stay importable outside Blender (SPEC §10) — that is
# what makes the solver debuggable with plain python3.

def _bpy_modules():
    from . import properties
    from .ops import draw as ops_draw
    from .ops import layout as ops_layout
    from .ops import lods as ops_lods
    from .ops import retarget as ops_retarget
    from .ui import overlay as ui_overlay
    from .ui import panel as ui_panel
    from .ui import tools as ui_tools
    return properties, (ops_layout, ops_draw, ops_lods, ops_retarget,
                        ui_panel, ui_tools, ui_overlay)


def register():
    properties, mods = _bpy_modules()
    properties.register()
    for mod in mods:
        mod.register()


def unregister():
    properties, mods = _bpy_modules()
    for mod in reversed(mods):
        mod.unregister()
    properties.unregister()
