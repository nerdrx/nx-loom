"""The result brush: tweak and relax the generated mesh, TopoGun-style.

Drag moves vertices with soft falloff; Shift-drag relaxes. Every sample is
re-projected onto the reference, so the mesh slides along the sculpt rather
than off it — and when the brush ends, the whole session is captured into
the delta layer in one go, so the massage survives every rebuild.
"""

from __future__ import annotations

import bpy
import numpy as np
from bpy_extras import view3d_utils

from ..core import brush as brush_mod
from ..core.graph import GRAPH_KEY
from ..ui import overlay
from .draw import _surface_of
from .layout import active_object, get_graph


def _view3d_region(context, event):
    for area in context.window.screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            x = event.mouse_x - region.x
            y = event.mouse_y - region.y
            if 0 <= x < region.width and 0 <= y < region.height:
                return region, area.spaces.active.region_3d, (x, y)
    return None, None, None


def stroke_step(verts, nbrs, ref_surface, center, radius, delta, mode):
    """One brush sample: displace, then glue back onto the reference.

    Pure enough to test headless — the modal only feeds it events.
    """
    if mode == "RELAX":
        out, hit = brush_mod.relax(verts, nbrs, center, radius)
    else:
        out, hit = brush_mod.tweak(verts, center, radius, delta)
    if ref_surface is not None and hit.any():
        out[hit] = np.asarray(ref_surface.project(out[hit]), dtype=float)
    return out, hit


class NXLOOM_OT_brush(bpy.types.Operator):
    """Massage the generated mesh: drag = tweak with falloff, Shift-drag =
    relax, wheel = radius, Esc/right-click = done (edits are captured)"""

    bl_idname = "nxloom.brush"
    bl_label = "Adjust Result"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj
                    and obj.type == "MESH" and len(obj.data.polygons))

    def invoke(self, context, event):
        obj = active_object(context)
        self._obj_name = obj.name
        graph = get_graph(obj)
        self._ref = _surface_of(graph, context)
        me = obj.data
        n = len(me.vertices)
        co = np.empty(n * 3)
        me.vertices.foreach_get("co", co)
        mw = np.asarray(obj.matrix_world, dtype=float)
        self._mw, self._mw_inv = mw, np.asarray(
            obj.matrix_world.inverted(), dtype=float)
        self._verts = co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
        self._nbrs = brush_mod.vert_adjacency(
            [tuple(p.vertices) for p in me.polygons], n)
        span = float(np.linalg.norm(self._verts.max(axis=0)
                                    - self._verts.min(axis=0))) or 1.0
        self._radius = span * 0.06
        self._pressed = False
        self._touched = False
        self._last2d = None
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Brush: drag = tweak, Shift-drag = relax, wheel = size, "
            "Esc = done (captures the edits)")
        return {"RUNNING_MODAL"}

    def _hit(self, context, event):
        region, rv3d, xy = _view3d_region(context, event)
        if region is None or self._ref is None:
            return None, None, None
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, xy)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, xy)
        from ..core.picking import ray_surface
        return ray_surface(self._ref, origin, direction), region, rv3d

    def _write(self, context):
        obj = bpy.data.objects.get(self._obj_name)
        if obj is None:
            return
        local = self._verts @ self._mw_inv[:3, :3].T + self._mw_inv[:3, 3]
        obj.data.vertices.foreach_set("co", local.reshape(-1))
        obj.data.update()

    def _finish(self, context):
        overlay.set_brush_cursor(None, 0.0)
        context.workspace.status_text_set(None)
        if self._touched:
            try:
                bpy.ops.nxloom.capture_edits()
                self.report({"INFO"},
                            "Brush session captured — the massage now "
                            "survives rebuilds")
            except RuntimeError as exc:
                self.report({"WARNING"}, f"Capture failed: {exc}")
        return {"FINISHED"} if self._touched else {"CANCELLED"}

    def modal(self, context, event):
        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            self._radius *= 1.15 if event.type == "WHEELUPMOUSE" else 1 / 1.15
            point, _r, _v = self._hit(context, event)
            overlay.set_brush_cursor(point, self._radius)
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE":
            point, region, rv3d = self._hit(context, event)
            overlay.set_brush_cursor(point, self._radius)
            if self._pressed and point is not None:
                delta = np.zeros(3)
                if self._last2d is not None and region is not None:
                    x = event.mouse_x - region.x
                    y = event.mouse_y - region.y
                    a = view3d_utils.region_2d_to_location_3d(
                        region, rv3d, (x, y), point)
                    b = view3d_utils.region_2d_to_location_3d(
                        region, rv3d, self._last2d, point)
                    delta = np.asarray(a - b, dtype=float)
                mode = "RELAX" if event.shift else "TWEAK"
                self._verts, hit = stroke_step(
                    self._verts, self._nbrs, self._ref, point,
                    self._radius, delta, mode)
                if hit.any():
                    self._touched = True
                    self._write(context)
            if region is not None:
                self._last2d = (event.mouse_x - region.x,
                                event.mouse_y - region.y)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            self._pressed = event.value == "PRESS"
            if not self._pressed:
                self._last2d = None
            return {"RUNNING_MODAL"}

        if event.type in {"ESC", "RIGHTMOUSE"}:
            return self._finish(context)
        return {"PASS_THROUGH"}


def register():
    bpy.utils.register_class(NXLOOM_OT_brush)


def unregister():
    bpy.utils.unregister_class(NXLOOM_OT_brush)
