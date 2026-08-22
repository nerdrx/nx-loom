bl_info = {
    "name": "NX Loom",
    "author": "nerdrx + Claude",
    "version": (0, 1, 0),
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
    from .ops import layout as ops_layout
    from .ui import panel as ui_panel
    return properties, (ops_layout, ui_panel)


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
