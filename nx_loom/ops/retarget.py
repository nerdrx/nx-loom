"""Retarget a layout onto another mesh."""

from __future__ import annotations

import bpy
import numpy as np
from bpy.props import EnumProperty, FloatProperty, PointerProperty

from ..core import retarget as rt
from ..core import symmetry as sym
from ..core.graph import GRAPH_KEY
from ..core.surface import Surface
from ..ui import overlay
from .layout import active_object, get_graph, rebuild_object, set_graph


def _armature_of(obj):
    for mod in obj.modifiers:
        if mod.type == "ARMATURE" and mod.object is not None:
            return mod.object
    if obj.parent is not None and obj.parent.type == "ARMATURE":
        return obj.parent
    return None


def bone_landmarks(src_obj, dst_obj):
    """Matching bone heads from two armatures, in world space.

    Two humanoid rigs already agree on their bone names, which is a dense and
    genuinely anatomical correspondence sitting right there — far better than
    anything derived from the shapes alone.
    """
    a, b = _armature_of(src_obj), _armature_of(dst_obj)
    if a is None or b is None:
        return None
    src, dst = [], []
    for name, bone in a.data.bones.items():
        other = b.data.bones.get(name)
        if other is None:
            continue
        src.append(tuple(a.matrix_world @ bone.head_local))
        dst.append(tuple(b.matrix_world @ other.head_local))
    if len(src) < 3:
        return None
    return np.array(src), np.array(dst)


def empty_landmarks(src_obj, dst_obj):
    """Empties parented to each object, matched by name."""
    def collect(o):
        return {c.name.split(".")[0]: tuple(c.matrix_world.translation)
                for c in o.children if c.type == "EMPTY"}
    a, b = collect(src_obj), collect(dst_obj)
    shared = sorted(set(a) & set(b))
    if len(shared) < 3:
        return None
    return (np.array([a[k] for k in shared]), np.array([b[k] for k in shared]))


class NXLOOM_OT_retarget(bpy.types.Operator):
    """Move this layout onto another mesh, keeping its topology"""

    bl_idname = "nxloom.retarget"
    bl_label = "Retarget Layout"
    bl_options = {"REGISTER", "UNDO"}

    method: EnumProperty(
        name="Landmarks",
        items=[
            ("AUTO", "Automatic", "Matching bone names, then matching empties, "
                                  "then bounding boxes"),
            ("BONES", "Matching Bones", "Bone heads shared by both armatures"),
            ("EMPTIES", "Matching Empties", "Empties parented to each mesh, "
                                            "matched by name"),
            ("BOUNDS", "Bounding Box", "Corner correspondence only — rough"),
        ],
        default="AUTO",
    )
    smoothing: FloatProperty(
        name="Smoothing", default=0.0, min=0.0, max=10.0,
        description="Relax the fit through the landmarks instead of hitting "
                    "them exactly. Raise it when landmarks are noisy",
    )

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        st = context.scene.nx_loom
        return bool(obj is not None and GRAPH_KEY in obj
                    and st.retarget_to is not None
                    and st.retarget_to.type == "MESH")

    def execute(self, context):
        obj = active_object(context)
        st = context.scene.nx_loom
        target = st.retarget_to
        graph = get_graph(obj)
        src_ref = bpy.data.objects.get(graph.reference) if graph.reference else None
        if src_ref is None:
            src_ref = st.reference
        if src_ref is None:
            self.report({"ERROR"}, "The layout has no reference to retarget from")
            return {"CANCELLED"}
        if target is src_ref:
            self.report({"ERROR"}, "Target and source are the same mesh")
            return {"CANCELLED"}

        depsgraph = context.evaluated_depsgraph_get()
        src_surface = Surface(src_ref, depsgraph)
        dst_surface = Surface(target, depsgraph)

        pairs, how = None, ""
        if self.method in ("AUTO", "BONES"):
            pairs = bone_landmarks(src_ref, target)
            how = "matching bones"
        if pairs is None and self.method in ("AUTO", "EMPTIES"):
            pairs = empty_landmarks(src_ref, target)
            how = "matching empties"
        if pairs is None and self.method in ("AUTO", "BOUNDS"):
            pairs = rt.bbox_landmarks(src_surface, dst_surface)
            how = "bounding boxes"
        if pairs is None:
            self.report({"ERROR"},
                        "No landmarks found. Give both meshes an armature with "
                        "shared bone names, or parent matching empties to each.")
            return {"CANCELLED"}

        before = (len(graph.nodes), len(graph.arcs), len(graph.patches))
        rep = rt.retarget(graph, dst_surface, pairs[0], pairs[1], self.smoothing)

        graph.reference = target.name
        sym.sync(graph, st.symmetry_axis, st.symmetry_tolerance, dst_surface)
        graph.discover_patches(normal_at=dst_surface.normal_at,
                               corner_angle=st.corner_angle)
        set_graph(obj, graph)
        st.reference = target
        obj.matrix_world = target.matrix_world.copy()
        rebuild_object(obj, context)
        overlay.mark_dirty()

        after = (len(graph.nodes), len(graph.arcs), len(graph.patches))
        note = "" if before[:2] == after[:2] else f" (was {before[:2]})"
        self.report(
            {"INFO"},
            f"Retargeted onto {target.name} via {rep['landmarks']} {how}: "
            f"{after[0]} nodes, {after[1]} arcs, {after[2]} patches{note}"
        )
        return {"FINISHED"}


_CLASSES = (NXLOOM_OT_retarget,)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
