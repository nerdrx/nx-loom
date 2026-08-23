"""Addon properties. The layout itself lives on the object, not here (SPEC §1)."""

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty)


def _apply_active_loops(self, context):
    """Typing a number applies it to the selected arc straight away."""
    from .ops import draw as draw_ops
    draw_ops.apply_active_loops(context, int(self.active_loops))


class NXLoomSettings(bpy.types.PropertyGroup):
    reference: PointerProperty(
        name="Reference",
        type=bpy.types.Object,
        description="Dense mesh the layout is pinned to and reprojected onto",
        poll=lambda self, obj: obj.type == "MESH",
    )
    size_mode: EnumProperty(
        name="Size By",
        description="Whether to aim for an edge length or a face budget",
        items=[
            ("EDGE", "Edge Length", "Target quad edge length"),
            ("COUNT", "Face Count", "Aim for a total number of faces — how "
                                    "game-asset budgets are actually specified"),
        ],
        default="EDGE",
    )
    target_count: IntProperty(
        name="Faces",
        description="Face budget. The exact number is not always reachable — "
                    "subdivisions are whole numbers — so the closest achievable "
                    "count is used and reported",
        default=2000, min=4, soft_max=200000,
    )
    target_edge: FloatProperty(
        name="Edge Length",
        description="Target quad edge length — bigger means coarser. One "
                    "slider re-grids the whole model and the quantizer "
                    "guarantees every patch still closes",
        default=0.1, min=0.0005, max=10.0, soft_max=1.0, unit="LENGTH",
    )
    relax_iters: IntProperty(
        name="Smoothing",
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
    arc_type: EnumProperty(
        name="Arc Type",
        description="Type given to newly drawn arcs",
        items=[
            ("flow", "Flow", "Ordinary edge-flow arc"),
            ("crease", "Crease", "Hard edge; the generated mesh is marked sharp here"),
            ("boundary", "Boundary", "Open border of the surface"),
            ("seam", "Seam", "UV seam / symmetry seam"),
        ],
        default="flow",
    )
    snap_pixels: FloatProperty(
        name="Snap",
        description="Snap radius in pixels. Defined on screen rather than in "
                    "world units so snapping feels the same at any zoom",
        default=18.0, min=2.0, max=80.0,
    )
    active_loops: IntProperty(
        name="Loops",
        description="Loop count across the selected arc. Typing here pins it "
                    "to exactly this number",
        default=2, min=1, max=4096,
        update=_apply_active_loops,
    )
    pick_pixels: FloatProperty(
        name="Pick",
        description="How close the cursor has to be to grab a node or an arc. "
                    "Kept separate from Snap: snapping while drawing should be "
                    "conservative, but grabbing something should be forgiving",
        default=26.0, min=4.0, max=120.0,
    )
    node_falloff: FloatProperty(
        name="Bend",
        description="Fraction of an arc that follows a node you drag. 1.0 "
                    "bends the whole arc; lower values keep the far end put",
        default=1.0, min=0.05, max=1.0,
    )
    stroke_smooth: FloatProperty(
        name="Stroke Smoothing",
        description="How much hand jitter to fair out of freehand strokes on "
                    "commit. A low-pass filter, not a straightener — 0 keeps "
                    "every wobble",
        default=0.35, min=0.0, max=1.0,
    )
    bridge_rings: BoolProperty(
        name="Bridge Rings",
        description="After a ring cut, connect it to the previous ring with "
                    "four wall arcs when the two plausibly sit on the same "
                    "limb — a tube of clean quads in two swipes",
        default=True,
    )
    rebuild_on_draw: BoolProperty(
        name="Rebuild While Drawing",
        description="Regenerate the mesh after each arc. Turn off on heavy "
                    "layouts and rebuild manually",
        default=True,
    )
    show_overlay: BoolProperty(
        name="Show Layout",
        description="Draw the layout graph in the viewport",
        default=True,
    )
    show_counts: BoolProperty(
        name="Show Loop Counts",
        description="Print the loop count on pinned arcs and on the arc under "
                    "the cursor",
        default=True,
    )
    show_fills: BoolProperty(
        name="State Fills",
        description="Tint failing patches red and the background region grey, "
                    "and outline holes, instead of leaving states to be "
                    "inferred from outlines",
        default=True,
    )
    show_ticks: BoolProperty(
        name="Subdivision Ticks",
        description="Dots along each arc where vertices will land, so density "
                    "is visible before a rebuild",
        default=True,
    )
    show_legend: BoolProperty(
        name="Colour Legend",
        description="A small key in the viewport corner for what every overlay "
                    "colour means",
        default=True,
    )
    overlay_xray: BoolProperty(
        name="Layout X-Ray",
        description="Draw the layout on top of everything instead of letting "
                    "the surface occlude it",
        default=True,
    )
    symmetry_axis: EnumProperty(
        name="Symmetry",
        description="Mirror the layout across this axis. Both halves share the "
                    "nodes sitting on the plane, so the seam is welded by "
                    "construction rather than merged afterwards",
        items=[
            ("NONE", "None", "No symmetry"),
            ("X", "X", "Mirror across the YZ plane"),
            ("Y", "Y", "Mirror across the XZ plane"),
            ("Z", "Z", "Mirror across the XY plane"),
        ],
        default="NONE",
    )
    symmetry_tolerance: FloatProperty(
        name="Seam Tolerance",
        description="Nodes within this distance of the mirror plane are "
                    "snapped onto it and shared by both halves",
        default=0.002, min=0.0, max=1.0, precision=4, unit="LENGTH",
    )
    mirror_edits: BoolProperty(
        name="Mirror Hand Edits",
        description="Copy captured vertex edits across the symmetry plane. "
                    "Off, an edit stays exactly where you made it",
        default=False,
    )
    retarget_to: PointerProperty(
        name="Retarget To",
        type=bpy.types.Object,
        description="Move this layout onto another mesh, keeping its topology",
        poll=lambda self, obj: obj.type == "MESH",
    )
    fill_background: BoolProperty(
        name="Fill Background Region",
        description="Fill the leftover region too. A loop drawn around a limb "
                    "splits a closed mesh into the limb and everything else; "
                    "by default that leftover is left alone instead of being "
                    "covered in geometry",
        default=False,
    )
    transfer_data: BoolProperty(
        name="Transfer Data on Apply",
        description="Carry UVs, materials, vertex groups, shape keys, creases "
                    "and bevel weights over from the reference when applying",
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
