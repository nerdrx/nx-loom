"""Stamp operators: place a library fragment as ghosts, save your own.

Placement is a modal gesture — move to aim, wheel to size, R to rotate,
click to stamp — and what a stamp leaves behind is suggestion ghosts, so
Accept/Discard and mirroring come from the existing lane. Headless (and for
scripts), execute() places at the 3D cursor instead.
"""

from __future__ import annotations

import json
import os

import bpy
import numpy as np
from bpy_extras import view3d_utils

from ..core import stamp as stamp_mod
from ..core.picking import ray_surface
from ..core.graph import GRAPH_KEY
from ..ui import overlay
from .draw import _surface_of
from .layout import active_object, get_graph, set_graph

# Tests point this at a scratch file; users get one under their config dir.
STAMP_FILE = None


def _stamp_path():
    if STAMP_FILE:
        return STAMP_FILE
    base = bpy.utils.user_resource("CONFIG", path="nx_loom", create=True)
    return os.path.join(base, "stamps.json")


def load_user_stamps():
    try:
        with open(_stamp_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k): v for k, v in data.items() if isinstance(v, list)}
    except (OSError, ValueError):
        return {}


def save_user_stamp(name, polys2d):
    stamps = load_user_stamps()
    stamps[str(name)] = polys2d
    path = _stamp_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(stamps, fh)


def _resolve(name):
    if name in stamp_mod.BUILTINS:
        return stamp_mod.builtin(name)
    hit = load_user_stamps().get(name)
    if hit is None:
        return None
    return [np.asarray(p, dtype=float) for p in hit]


# Dynamic EnumProperty items must stay referenced from Python or the strings
# are freed under Blender's feet — the classic dynamic-enum trap.
_ITEMS = []


def stamp_items(_self, _context):
    global _ITEMS
    items = [(n, n.replace("_", " ").title(), "Built-in stamp")
             for n in stamp_mod.BUILTINS]
    items += [(n, n, "Saved stamp") for n in sorted(load_user_stamps())]
    _ITEMS = items
    return _ITEMS


def _tangent_frame(surface, point, prefer=None):
    n = np.asarray(surface.normal_at(point), dtype=float)
    nn = np.linalg.norm(n)
    n = n / nn if nn > 0 else np.array([0.0, 0.0, 1.0])
    cand = [np.asarray(prefer, dtype=float)] if prefer is not None else []
    cand += [np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0])]
    for c in cand:
        e1 = c - n * float(c @ n)
        ln = np.linalg.norm(e1)
        if ln > 1e-6:
            e1 /= ln
            return e1, np.cross(n, e1)
    return np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])


def _view3d_hit(context, event, surface):
    """Ray under the mouse in whichever 3D viewport it is over."""
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
            origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
            direction = view3d_utils.region_2d_to_vector_3d(
                region, rv3d, (x, y))
            hit = ray_surface(surface, origin, direction)
            if hit is not None:
                right = np.asarray(
                    rv3d.view_matrix.inverted().col[0][:3], dtype=float)
                return hit, right
    return None, None


def _as_ghosts(polys):
    return [[float(x) for p in poly for x in p] for poly in polys]


class NXLOOM_OT_stamp_place(bpy.types.Operator):
    """Drop a topology stamp as suggestion ghosts — move to aim, wheel to
    size, R to rotate, click to stamp, Esc to cancel. Accept commits it"""

    bl_idname = "nxloom.stamp_place"
    bl_label = "Place Stamp"
    bl_options = {"REGISTER", "UNDO"}

    stamp: bpy.props.EnumProperty(name="Stamp", items=stamp_items)
    scale: bpy.props.FloatProperty(
        name="Size", default=0.0, min=0.0,
        description="Stamp radius. 0 picks ~8% of the reference size")

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj)

    def _setup(self, context):
        obj = active_object(context)
        self._obj_name = obj.name
        graph = get_graph(obj)
        surface = _surface_of(graph, context)
        if surface is None:
            self.report({"ERROR"}, "Set a Reference mesh first")
            return None
        polys = _resolve(self.stamp)
        if polys is None:
            self.report({"ERROR"}, f"Unknown stamp '{self.stamp}'")
            return None
        span = float(np.linalg.norm(surface.verts.max(axis=0)
                                    - surface.verts.min(axis=0)))
        if self.scale <= 0.0:
            self.scale = span * 0.08
        return graph, surface, polys

    def _commit(self, context, graph, ghosts):
        obj = bpy.data.objects.get(self._obj_name)
        if obj is None:
            return
        stored = list(graph.settings.get("suggestions") or [])
        graph.settings["suggestions"] = stored + ghosts
        set_graph(obj, graph)
        overlay.mark_dirty()
        self.report({"INFO"},
                    f"Stamp placed as {len(ghosts)} ghost arc(s) — "
                    f"accept or discard them")

    def execute(self, context):
        got = self._setup(context)
        if got is None:
            return {"CANCELLED"}
        graph, surface, polys = got
        point = np.asarray(surface.project(np.asarray(
            [tuple(context.scene.cursor.location)], dtype=float))[0],
            dtype=float)
        e1, e2 = _tangent_frame(surface, point)
        placed = stamp_mod.place(polys, point, e1, e2, self.scale,
                                 project=surface.project)
        self._commit(context, graph, _as_ghosts(placed))
        return {"FINISHED"}

    def invoke(self, context, event):
        got = self._setup(context)
        if got is None:
            return {"CANCELLED"}
        self._graph, self._surface, self._polys = got
        self._rot = 0.0
        self._last = None
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Stamp: move to aim, wheel = size, R = rotate, "
            "click = place, Esc = cancel")
        return {"RUNNING_MODAL"}

    def _preview(self, context, event):
        point, right = _view3d_hit(context, event, self._surface)
        if point is None:
            overlay.set_stamp_preview(None)
            self._last = None
            return
        e1, e2 = _tangent_frame(self._surface, point, prefer=right)
        placed = stamp_mod.place(self._polys, point, e1, e2, self.scale,
                                 rot=self._rot, project=self._surface.project)
        self._last = placed
        overlay.set_stamp_preview(placed)

    def modal(self, context, event):
        if event.type == "MOUSEMOVE":
            self._preview(context, event)
            return {"RUNNING_MODAL"}
        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            self.scale *= 1.12 if event.type == "WHEELUPMOUSE" else 1 / 1.12
            self._preview(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "R" and event.value == "PRESS":
            self._rot += np.pi / 12
            self._preview(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            overlay.set_stamp_preview(None)
            context.workspace.status_text_set(None)
            if self._last is None:
                self.report({"WARNING"}, "Nothing under the cursor")
                return {"CANCELLED"}
            self._commit(context, self._graph, _as_ghosts(self._last))
            return {"FINISHED"}
        if event.type in {"ESC", "RIGHTMOUSE"}:
            overlay.set_stamp_preview(None)
            context.workspace.status_text_set(None)
            return {"CANCELLED"}
        return {"PASS_THROUGH"}


class NXLOOM_OT_stamp_save(bpy.types.Operator):
    """Save the arcs around the 3D cursor as a named stamp in your library.
    Place the cursor (Shift+RMB) on the region first and set the radius"""

    bl_idname = "nxloom.stamp_save"
    bl_label = "Save Stamp"

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj)

    def execute(self, context):
        st = context.scene.nx_loom
        name = st.stamp_name.strip()
        if not name:
            self.report({"ERROR"}, "Name the stamp first")
            return {"CANCELLED"}
        obj = active_object(context)
        graph = get_graph(obj)
        centre = np.asarray(tuple(context.scene.cursor.location), dtype=float)
        radius = float(st.stamp_radius)
        polys = []
        for arc in graph.arcs.values():
            if arc.mirror_of is not None:
                continue
            path = np.asarray(arc.path, dtype=float)
            if len(path) and float(np.linalg.norm(path - centre,
                                                  axis=1).max()) <= radius:
                polys.append(path)
        if not polys:
            self.report({"ERROR"},
                        f"No arcs fully inside {radius:.2f} of the 3D "
                        f"cursor — move the cursor onto the region or "
                        f"raise the radius")
            return {"CANCELLED"}
        flat = stamp_mod.normalize(polys)
        if flat is None:
            self.report({"ERROR"}, "Those arcs have no extent to normalise")
            return {"CANCELLED"}
        save_user_stamp(name, flat)
        self.report({"INFO"},
                    f"Stamp '{name}' saved ({len(polys)} arc(s)) — it is "
                    f"now in the Place Stamp list")
        return {"FINISHED"}


_CLASSES = (NXLOOM_OT_stamp_place, NXLOOM_OT_stamp_save)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
