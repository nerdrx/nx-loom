"""Map baking: the pipeline actually ends here.

Normal (and optionally AO) baked from the reference sculpt onto the
generated mesh's layout UVs, one click, with the cage extrusion computed
from the real distance between the two meshes instead of a guess. Images
are packed into the .blend so nothing dangles.
"""

from __future__ import annotations

import bpy
import numpy as np

from ..core.graph import GRAPH_KEY
from .draw import _surface_of
from .layout import active_object, get_graph


def _cage_extrusion(obj, surface):
    """How far the low mesh sits from the sculpt, padded — rays must clear
    the real gap or the bake reads the wrong side of the surface."""
    me = obj.data
    n = len(me.vertices)
    co = np.empty(n * 3)
    me.vertices.foreach_get("co", co)
    mw = np.asarray(obj.matrix_world, dtype=float)
    world = co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
    proj = np.asarray(surface.project(world), dtype=float)
    gap = float(np.linalg.norm(proj - world, axis=1).max())
    span = float(np.linalg.norm(surface.verts.max(axis=0)
                                - surface.verts.min(axis=0))) or 1.0
    return gap * 2.0 + span * 0.005


class NXLOOM_OT_bake_maps(bpy.types.Operator):
    """Bake normal (and optionally AO) maps from the reference sculpt onto
    the layout UVs. Generates the UVs first if they are missing"""

    bl_idname = "nxloom.bake_maps"
    bl_label = "Bake Maps"
    bl_options = {"REGISTER", "UNDO"}

    resolution: bpy.props.EnumProperty(
        name="Resolution",
        items=[("128", "128", ""), ("256", "256", ""),
               ("512", "512", ""), ("1024", "1024", ""),
               ("2048", "2048", ""), ("4096", "4096", "")],
        default="1024")
    do_ao: bpy.props.BoolProperty(
        name="Also Bake AO", default=False,
        description="Ambient occlusion alongside the normal map")

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj
                    and obj.type == "MESH" and len(obj.data.polygons))

    def execute(self, context):
        obj = active_object(context)
        graph = get_graph(obj)
        ref = bpy.data.objects.get(graph.reference) if graph.reference \
            else None
        if ref is None:
            ref = context.scene.nx_loom.reference
        if ref is None:
            self.report({"ERROR"}, "Set a Reference mesh first")
            return {"CANCELLED"}
        surface = _surface_of(graph, context)
        if surface is None:
            self.report({"ERROR"}, "Could not read the reference")
            return {"CANCELLED"}

        if "NXLoom" not in obj.data.uv_layers:
            try:
                res = bpy.ops.nxloom.generate_uvs()
            except RuntimeError:
                res = {"CANCELLED"}
            if "FINISHED" not in res:
                self.report({"ERROR"},
                            "No UVs and generating them failed — rebuild "
                            "the layout first")
                return {"CANCELLED"}
        obj.data.uv_layers.active = obj.data.uv_layers["NXLoom"]

        size = int(self.resolution)
        extrusion = _cage_extrusion(obj, surface)
        scene = context.scene
        state = (scene.render.engine,
                 [(o, o.select_get()) for o in scene.objects],
                 ref.hide_get(), ref.hide_render,
                 list(obj.data.materials))
        images = []
        tmp_mat = bpy.data.materials.new("NXLoom_Bake")
        tmp_mat.use_nodes = True
        tex_node = tmp_mat.node_tree.nodes.new("ShaderNodeTexImage")
        tmp_mat.node_tree.nodes.active = tex_node
        try:
            scene.render.engine = "CYCLES"
            scene.cycles.device = "CPU"
            scene.render.bake.use_selected_to_active = True
            scene.render.bake.cage_extrusion = extrusion
            scene.render.bake.max_ray_distance = extrusion * 2.0
            scene.render.bake.margin = max(size // 64, 4)

            obj.data.materials.clear()
            obj.data.materials.append(tmp_mat)
            for o in scene.objects:
                o.select_set(False)
            ref.hide_set(False)
            ref.hide_render = False
            ref.select_set(True)
            obj.select_set(True)
            context.view_layer.objects.active = obj

            jobs = [("NORMAL", f"{obj.name}_normal", True, 1)]
            if self.do_ao:
                jobs.append(("AO", f"{obj.name}_ao", False, 16))
            for bake_type, name, non_color, samples in jobs:
                img = bpy.data.images.get(name)
                if img is None or tuple(img.size) != (size, size):
                    if img is not None:
                        bpy.data.images.remove(img)
                    img = bpy.data.images.new(name, size, size,
                                              float_buffer=not non_color)
                if non_color:
                    img.colorspace_settings.name = "Non-Color"
                tex_node.image = img
                scene.cycles.samples = samples
                kwargs = {"type": bake_type}
                if bake_type == "NORMAL":
                    kwargs["normal_space"] = "TANGENT"
                res = bpy.ops.object.bake(**kwargs)
                if "FINISHED" not in res:
                    self.report({"ERROR"}, f"{bake_type} bake failed")
                    return {"CANCELLED"}
                img.pack()
                images.append(name)
        finally:
            engine, selection, ref_hide, ref_hide_r, mats = state
            obj.data.materials.clear()
            for m in mats:
                obj.data.materials.append(m)
            bpy.data.materials.remove(tmp_mat)
            scene.render.engine = engine
            for o, sel in selection:
                try:
                    o.select_set(sel)
                except ReferenceError:
                    pass
            ref.hide_set(ref_hide)
            ref.hide_render = ref_hide_r

        self.report({"INFO"},
                    f"Baked {', '.join(images)} at {size}px (packed into "
                    f"the .blend) — cage {extrusion:.4f}")
        return {"FINISHED"}


def register():
    bpy.utils.register_class(NXLOOM_OT_bake_maps)


def unregister():
    bpy.utils.unregister_class(NXLOOM_OT_bake_maps)
