"""The addon registers, unregisters, and re-registers cleanly."""

import bpy


def run():
    out = []
    import nx_loom
    try:
        nx_loom.register()
        out.append(("register", True, ""))
    except Exception as e:
        return [("register", False, repr(e))]

    out.append(("scene property", hasattr(bpy.context.scene, "nx_loom"), ""))
    for op in ("layout_from_selection", "rebuild", "apply"):
        out.append((f"op nxloom.{op}", hasattr(bpy.ops.nxloom, op), ""))

    try:
        nx_loom.unregister()
        nx_loom.register()
        out.append(("re-register", True, ""))
    except Exception as e:
        out.append(("re-register", False, repr(e)))
    return out
