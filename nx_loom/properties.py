"""Addon properties. The layout itself lives on the object, not here (SPEC §1)."""

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, PointerProperty


class NXLoomSettings(bpy.types.PropertyGroup):
    reference: PointerProperty(
        name="Reference",
        type=bpy.types.Object,
        description="Dense mesh the layout is pinned to and reprojected onto",
        poll=lambda self, obj: obj.type == "MESH",
    )
    target_edge: FloatProperty(
        name="Density",
        description="Target edge length. One slider re-grids the whole model; "
                    "the quantizer guarantees every patch still closes",
        default=0.1, min=0.0005, max=10.0, soft_max=1.0, unit="LENGTH",
    )
    relax_iters: IntProperty(
        name="Relax",
        description="Laplacian smoothing passes on patch interiors before "
                    "reprojection. 0 leaves the raw Coons interpolation",
        default=20, min=0, max=200,
    )
    corner_angle: FloatProperty(
        name="Corner Angle",
        description="A layout node turning more sharply than this starts a new "
                    "patch side, even when its valence is 2",
        default=50.0, min=1.0, max=179.0,
    )
    reproject: BoolProperty(
        name="Reproject",
        description="Snap generated interior vertices back onto the reference",
        default=True,
    )
    auto_rebuild: BoolProperty(
        name="Auto Rebuild",
        description="Rebuild whenever the layout or density changes",
        default=False,
    )


_CLASSES = (NXLoomSettings,)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.nx_loom = PointerProperty(type=NXLoomSettings)


def unregister():
    del bpy.types.Scene.nx_loom
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
