"""LOD sets from one layout.

The point is not that we can decimate — anything can decimate. It is that
re-solving at a smaller budget changes the subdivision counts and **nothing
else**: same patches, same poles, same seams, same material boundaries. Every
level is the same surface at a different resolution, so UVs, weights and shape
keys transfer onto all of them from the same source and match across levels.

That is the part a decimator cannot promise, and it is the reason LODs are
normally painful.
"""

from __future__ import annotations

import bpy
import numpy as np
from bpy.props import BoolProperty, FloatProperty, IntProperty
from mathutils import Vector

from ..core import delta as delta_mod
from ..core import symmetry as sym
from ..core.build import build, floor_faces, solve_edge_for_count
from ..core.graph import GRAPH_KEY
from .layout import active_object, get_graph, _surface_for


def _emit(obj, graph, context, edge, name, surface, project):
    st = context.scene.nx_loom
    verts, quads, prov, report = build(
        graph, target_edge=edge, project=project,
        relax_iters=st.relax_iters, fill_background=st.fill_background,
    )
    if st.symmetry_axis != "NONE" and len(verts):
        verts, _ = sym.symmetrize_verts(verts, st.symmetry_axis,
                                        st.symmetry_tolerance)
    table = delta_mod.load(obj)
    if table["offsets"] and len(verts):
        normal_fn = surface.normal_at if surface else (lambda p: (0.0, 0.0, 1.0))
        verts, _ = delta_mod.apply_deltas(verts, prov, normal_fn, table)

    mesh = bpy.data.meshes.new(name)
    new_obj = bpy.data.objects.new(name, mesh)
    new_obj.matrix_world = obj.matrix_world.copy()
    mw_inv = obj.matrix_world.inverted()
    mesh.from_pydata([tuple(mw_inv @ Vector(tuple(v))) for v in verts], [], quads)
    mesh.update()
    return new_obj, report


class NXLOOM_OT_make_lods(bpy.types.Operator):
    """Emit a set of LODs from this layout, all sharing its structure"""

    bl_idname = "nxloom.make_lods"
    bl_label = "Make LODs"
    bl_options = {"REGISTER", "UNDO"}

    levels: IntProperty(
        name="Levels", default=3, min=2, max=8,
        description="How many levels to emit, including LOD0",
    )
    ratio: FloatProperty(
        name="Falloff", default=0.4, min=0.05, max=0.95,
        description="Face count of each level relative to the one before it",
    )
    transfer: BoolProperty(
        name="Transfer Data", default=True,
        description="Carry the reference's UVs, materials, weights and shape "
                    "keys onto every level",
    )

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj)

    def execute(self, context):
        obj = active_object(context)
        graph = get_graph(obj)
        st = context.scene.nx_loom
        surface = _surface_for(graph, context)
        if surface is not None:
            graph.refresh_positions(surface)
        sym.sync(graph, st.symmetry_axis, st.symmetry_tolerance, surface)
        graph.discover_patches(
            normal_at=surface.normal_at if surface else None,
            corner_angle=st.corner_angle)
        project = surface.project if (surface and st.reproject) else None

        base = len(obj.data.polygons)
        if st.size_mode == "COUNT":
            base = st.target_count
        if base < 4:
            self.report({"ERROR"}, "Rebuild first — there is nothing to scale from")
            return {"CANCELLED"}

        coll = bpy.data.collections.get(f"{obj.name}_LODs")
        if coll is None:
            coll = bpy.data.collections.new(f"{obj.name}_LODs")
            context.scene.collection.children.link(coll)

        floor = floor_faces(graph, st.fill_background)
        made, counts, clamped = [], [], False
        for i in range(self.levels):
            want = max(int(round(base * (self.ratio ** i))), 4)
            if counts and counts[-1] <= floor:
                # Emitting further levels would repeat the coarsest one. The
                # layout, not the solver, is the limit here.
                clamped = True
                break
            edge, _ = solve_edge_for_count(graph, want, st.fill_background)
            lod, report = _emit(obj, graph, context, edge,
                                f"{obj.name}_LOD{i}", surface, project)
            coll.objects.link(lod)
            made.append(lod)
            counts.append(len(lod.data.polygons))

        if self.transfer:
            ref = bpy.data.objects.get(graph.reference) if graph.reference else None
            if ref is None:
                ref = st.reference
            if ref is not None and ref.type == "MESH":
                from ..core.vendor import qf_transfer
                snap = None
                try:
                    snap = qf_transfer.capture(ref)
                    for lod in made:
                        qf_transfer.apply(snap, lod)
                except Exception as e:
                    self.report({"WARNING"}, f"Data transfer failed: {e}")
                finally:
                    if snap is not None:
                        try:
                            snap.free()
                        except Exception:
                            pass

        msg = f"{len(made)} LODs: {counts} faces"
        if clamped:
            msg += (f" — stopped at this layout's floor of {floor}. "
                    "Fewer faces needs a coarser layout, not a coarser solve.")
            self.report({"WARNING"}, msg)
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


_CLASSES = (NXLOOM_OT_make_lods,)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
