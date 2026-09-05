"""Field combing: brush direction hints that steer the suggestion field.

A comb stroke is not geometry — it is a preference. Suggest's cross field
treats comb tangents as SOFT pins: they outweigh the surface's curvature
opinion but yield to authored arcs and, gently, to their neighbours. Comb
where the flow should run, then Suggest; the proposals follow your comb.
Strokes live in ``settings["comb"]`` and are cleared in one click.
"""

from __future__ import annotations

import bpy
import numpy as np
from bpy_extras import view3d_utils

from ..core.graph import GRAPH_KEY
from ..core.picking import ray_surface
from ..ui import overlay
from .draw import _surface_of
from .layout import active_object, get_graph, set_graph


def add_comb(graph, polyline):
    """Store one comb stroke. Returns the new stroke count."""
    stored = list(graph.settings.get("comb") or [])
    stored.append([float(x) for p in np.asarray(polyline, dtype=float)
                   for x in p])
    graph.settings["comb"] = stored
    return len(stored)


class NXLOOM_OT_comb(bpy.types.Operator):
    """Comb the suggestion field: drag strokes where the flow should run.
    Hints only — they steer Suggest, they are not arcs. Esc when done"""

    bl_idname = "nxloom.comb"
    bl_label = "Comb Field"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj)

    def invoke(self, context, event):
        obj = active_object(context)
        self._obj_name = obj.name
        self._graph = get_graph(obj)
        self._surface = _surface_of(self._graph, context)
        if self._surface is None:
            self.report({"ERROR"}, "Set a Reference mesh first")
            return {"CANCELLED"}
        span = float(np.linalg.norm(self._surface.verts.max(axis=0)
                                    - self._surface.verts.min(axis=0))) or 1.0
        self._min_step = span * 0.005
        self._stroke = []
        self._pressed = False
        self._made = 0
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Comb: drag strokes where the flow should run "
            "(hints, not arcs) — Esc/right-click when done")
        return {"RUNNING_MODAL"}

    def _hit(self, context, event):
        for area in context.window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for region in area.regions:
                if region.type != "WINDOW":
                    continue
                x = event.mouse_x - region.x
                y = event.mouse_y - region.y
                if not (0 <= x < region.width and 0 <= y < region.height):
                    continue
                rv3d = area.spaces.active.region_3d
                if rv3d is None:
                    continue
                origin = view3d_utils.region_2d_to_origin_3d(
                    region, rv3d, (x, y))
                direction = view3d_utils.region_2d_to_vector_3d(
                    region, rv3d, (x, y))
                return ray_surface(self._surface, origin, direction)
        return None

    def _end_stroke(self, context):
        if len(self._stroke) >= 4:
            obj = bpy.data.objects.get(self._obj_name)
            if obj is not None:
                add_comb(self._graph, np.asarray(self._stroke))
                set_graph(obj, self._graph)
                overlay.mark_dirty()
                self._made += 1
        self._stroke = []

    def modal(self, context, event):
        if event.type == "MOUSEMOVE":
            if self._pressed:
                point = self._hit(context, event)
                if point is not None:
                    if not self._stroke or float(np.linalg.norm(
                            point - self._stroke[-1])) >= self._min_step:
                        self._stroke.append(point)
                        overlay.set_preview(
                            path=np.asarray(self._stroke)
                            if len(self._stroke) > 1 else None)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                self._pressed = True
                self._stroke = []
            else:
                self._pressed = False
                overlay.set_preview(path=None)
                self._end_stroke(context)
            return {"RUNNING_MODAL"}
        if event.type in {"ESC", "RIGHTMOUSE"}:
            overlay.set_preview(path=None)
            context.workspace.status_text_set(None)
            if self._made:
                self.report({"INFO"},
                            f"{self._made} comb stroke(s) — Suggest will "
                            f"follow them")
                return {"FINISHED"}
            return {"CANCELLED"}
        return {"PASS_THROUGH"}


class NXLOOM_OT_comb_clear(bpy.types.Operator):
    """Remove every comb stroke — the field goes back to pure curvature"""

    bl_idname = "nxloom.comb_clear"
    bl_label = "Clear Combs"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        if obj is None or GRAPH_KEY not in obj:
            return False
        graph = get_graph(obj)
        return bool(graph and graph.settings.get("comb"))

    def execute(self, context):
        obj = active_object(context)
        graph = get_graph(obj)
        n = len(graph.settings.get("comb") or [])
        graph.settings["comb"] = []
        set_graph(obj, graph)
        overlay.mark_dirty()
        self.report({"INFO"}, f"{n} comb stroke(s) cleared")
        return {"FINISHED"}


_CLASSES = (NXLOOM_OT_comb, NXLOOM_OT_comb_clear)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
