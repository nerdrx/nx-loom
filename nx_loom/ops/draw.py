"""The drawing tools.

A modal shell over :mod:`nx_loom.core.authoring` and :mod:`nx_loom.core.picking`.
Everything that can be wrong lives in those two modules and is tested without a
viewport; what is left here is mouse plumbing.
"""

from __future__ import annotations

import bpy
import numpy as np
from mathutils import Vector

from ..core.authoring import (add_arc, decimate, dissolve_node, move_node,
                              nearest_node, nearest_on_arc, remove_arc,
                              resolve_anchor)
from ..core.graph import GRAPH_KEY
from ..core.picking import (interp_rays, pixel_radius_world, ray_surface,
                            screen_ray, trace_rays)
from ..core.surface import Surface, cached_surface
from ..ui import overlay
from .layout import active_object, get_graph, rebuild_object, set_graph

DRAG_PIXELS = 6
SEGMENT_SAMPLES = 24


# -- shared plumbing --------------------------------------------------------

def _context_ok(context):
    obj = active_object(context)
    return bool(obj is not None and GRAPH_KEY in obj
                and getattr(context, "region", None) is not None
                and getattr(getattr(context, "space_data", None), "region_3d", None)
                is not None)


def _surface_of(graph, context):
    ref = bpy.data.objects.get(graph.reference) if graph.reference else None
    if ref is None:
        ref = context.scene.nx_loom.reference
    if ref is None:
        return None
    return cached_surface(ref, context.evaluated_depsgraph_get())


def _mouse_ray(context, event):
    return screen_ray(context.region, context.space_data.region_3d,
                      (event.mouse_region_x, event.mouse_region_y))


def _snap_radius(context, point):
    return pixel_radius_world(context.region, context.space_data.region_3d,
                              point, context.scene.nx_loom.snap_pixels)


def _pick_radius(context, point):
    """Grabbing something should be more forgiving than snapping to it."""
    return pixel_radius_world(context.region, context.space_data.region_3d,
                              point, context.scene.nx_loom.pick_pixels)


def commit_arc(graph, surface, rays, snap_radius, min_step,
               arc_type="flow", start_node=None, rail="surface"):
    """Trace rays onto the surface and add the resulting arc.

    Returns (arc_id, start_node, end_node), or None when the stroke produced
    nothing usable — off-surface, too short, or a loop back onto its own start.
    """
    path = trace_rays(surface, rays, min_step=min_step * 0.25)
    return commit_path(graph, surface, path, snap_radius, min_step,
                       arc_type, start_node, rail)


def commit_path(graph, surface, path, snap_radius, min_step,
                arc_type="flow", start_node=None, rail="surface"):
    """Add an arc from an already-traced surface path."""
    path = np.asarray(path, dtype=float)
    if len(path) < 2:
        return None

    a = start_node
    if a is None:
        a = resolve_anchor(graph, path[0], snap_radius, surface)[0]
    b = resolve_anchor(graph, path[-1], snap_radius, surface)[0]
    if a == b:
        return None
    path = decimate(path, min_step)
    if len(path) < 2:
        path = np.vstack([graph.nodes[a].co, graph.nodes[b].co])
    aid = add_arc(graph, a, b, path, surface, type=arc_type, rail=rail)
    return aid, a, b


def refresh(obj, graph, context, rebuild=True):
    """Re-derive patches, optionally regenerate, and record what failed."""
    from ..core import symmetry as sym
    st = context.scene.nx_loom
    surface = _surface_of(graph, context)
    normal_at = surface.normal_at if surface else None
    sym.sync(graph, st.symmetry_axis, st.symmetry_tolerance, surface)
    graph.discover_patches(normal_at=normal_at, corner_angle=st.corner_angle)
    set_graph(obj, graph)
    bad = []
    if rebuild and graph.patches:
        rep = rebuild_object(obj, context)
        if rep:
            bad = sorted(set(rep["unsatisfied_patches"])
                         | {pid for pid, why in rep["failed_patches"]
                            if why != "background"})
            obj["nx_loom_background"] = list(rep.get("background", []))
    obj["nx_loom_bad_patches"] = bad
    overlay.mark_dirty()
    return bad


# -- draw -------------------------------------------------------------------

class NXLOOM_OT_draw_arc(bpy.types.Operator):
    """Draw layout arcs on the reference surface.

    Click to chain straight-on-surface segments, or click and drag to draw
    freehand. Both snap to existing nodes and split arcs you cross.
    """

    bl_idname = "nxloom.draw_arc"
    bl_label = "Draw Arc"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return _context_ok(context)

    def invoke(self, context, event):
        obj = active_object(context)
        self.graph = get_graph(obj)
        if self.graph is None:
            self.report({"ERROR"}, "No layout on this object")
            return {"CANCELLED"}
        self.surface = _surface_of(self.graph, context)
        if self.surface is None:
            self.report({"ERROR"}, "Set a Reference mesh first")
            return {"CANCELLED"}

        span = float(np.linalg.norm(self.surface.verts.max(axis=0)
                                    - self.surface.verts.min(axis=0)))
        self.min_step = max(span * 0.004, 1e-6)
        self.anchor = None           # pending chain node
        self.pressed = False
        self.dragging = False
        self.press_xy = None
        self.press_ray = None
        self.stroke = []
        self.stroke_pts = []
        self.made = 0

        context.window.cursor_modal_set("PAINT_BRUSH")
        context.window_manager.modal_handler_add(self)
        self._hover(context, event)
        self._status(context)
        return {"RUNNING_MODAL"}

    # -- helpers

    def _status(self, context):
        context.workspace.status_text_set(
            "Click: chain segment   Drag: freehand   "
            "Esc/RMB: end chain, again to finish   Enter: finish"
        )

    def _surface_point(self, context, event):
        origin, direction = _mouse_ray(context, event)
        return ray_surface(self.surface, origin, direction), (origin, direction)

    def _snap_preview(self, context, point):
        if point is None:
            return None
        r = _snap_radius(context, point)
        hit = nearest_node(self.graph, point, r)
        if hit is not None:
            return self.graph.nodes[hit[0]].co
        hit = nearest_on_arc(self.graph, point, r)
        return hit[3] if hit is not None else None

    def _hover(self, context, event):
        point, ray = self._surface_point(context, event)
        snap = self._snap_preview(context, point)
        path = None
        if self.anchor is not None and point is not None:
            path = self._segment_path(context, self.graph.nodes[self.anchor].co, ray)
        overlay.set_preview(path=path, snap=snap,
                            anchor=None if self.anchor is None
                            else self.graph.nodes[self.anchor].co)

    def _segment_path(self, context, from_co, to_ray):
        """Trace a straight-on-screen segment from a world point to a ray."""
        from bpy_extras import view3d_utils
        region, rv3d = context.region, context.space_data.region_3d
        p2d = view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(tuple(from_co)))
        if p2d is None:
            return None
        a_ray = screen_ray(region, rv3d, (p2d.x, p2d.y))
        rays = interp_rays(a_ray, to_ray, SEGMENT_SAMPLES)
        path = trace_rays(self.surface, rays)
        return path if len(path) >= 2 else None

    def _commit_traced(self, context, path):
        """Commit a stroke that has already been traced onto the surface."""
        if len(path) < 2:
            return False
        radius = _snap_radius(context, path[-1])
        res = commit_path(self.graph, self.surface, path, radius, self.min_step,
                          arc_type=context.scene.nx_loom.arc_type,
                          start_node=self.anchor, rail="surface")
        return self._after_commit(context, res)

    def _commit(self, context, rays):
        obj = active_object(context)
        point = None
        probe = trace_rays(self.surface, rays[-1:])
        if len(probe):
            point = probe[0]
        radius = _snap_radius(context, point) if point is not None else self.min_step
        # reached only from a click-to-click segment; a drag commits its
        # already-traced path through _commit_traced and stays "surface"
        res = commit_arc(self.graph, self.surface, rays, radius, self.min_step,
                         arc_type=context.scene.nx_loom.arc_type,
                         start_node=self.anchor, rail="straight")
        return self._after_commit(context, res)

    def _after_commit(self, context, res):
        if res is None:
            return False
        obj = active_object(context)
        _, _, end = res
        self.anchor = end
        self.made += 1
        bad = refresh(obj, self.graph, context,
                      rebuild=context.scene.nx_loom.rebuild_on_draw)
        self.graph = get_graph(obj)
        if self.anchor not in self.graph.nodes:
            self.anchor = None
        if bad:
            context.workspace.status_text_set(
                f"{len(bad)} patch(es) unresolved — add an arc or change density"
            )
        else:
            self._status(context)
        return True

    def _finish(self, context, cancelled=False):
        overlay.clear_preview()
        overlay.clear_hover()
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)
        obj = active_object(context)
        if self.made:
            refresh(obj, self.graph, context, rebuild=True)
            bpy.ops.ed.undo_push(message=f"NX Loom: draw {self.made} arc(s)")
            self.report({"INFO"}, f"{self.made} arc(s), "
                                  f"{len(self.graph.patches)} patches")
        return {"CANCELLED"} if cancelled and not self.made else {"FINISHED"}

    # -- modal

    def modal(self, context, event):
        if context.region is None:
            return self._finish(context, cancelled=True)

        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"} or \
                (event.type.startswith("NUMPAD_") and event.type != "NUMPAD_ENTER"):
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            if self.pressed:
                dx = event.mouse_region_x - self.press_xy[0]
                dy = event.mouse_region_y - self.press_xy[1]
                if not self.dragging and (dx * dx + dy * dy) >= DRAG_PIXELS ** 2:
                    self.dragging = True
                    self.stroke = [self.press_ray]
                    seed = trace_rays(self.surface, [self.press_ray])
                    self.stroke_pts = [seed[0]] if len(seed) else []
                if self.dragging:
                    # Trace only the new sample. Re-tracing the whole stroke on
                    # every mouse-move is quadratic in stroke length, and on a
                    # dense sculpt that alone made dragging unusable.
                    ray = _mouse_ray(context, event)
                    self.stroke.append(ray)
                    prev = self.stroke_pts[-1] if self.stroke_pts else None
                    pts = trace_rays(self.surface, [ray], anchor=prev)
                    if len(pts):
                        if prev is None or float(
                                np.linalg.norm(pts[0] - prev)) >= self.min_step * 0.25:
                            self.stroke_pts.append(pts[0])
                    overlay.set_preview(
                        path=np.asarray(self.stroke_pts) if len(self.stroke_pts) > 1
                        else None,
                        anchor=None if self.anchor is None
                        else self.graph.nodes[self.anchor].co)
                    return {"RUNNING_MODAL"}
            self._hover(context, event)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            self.pressed = True
            self.dragging = False
            self.press_xy = (event.mouse_region_x, event.mouse_region_y)
            self.press_ray = _mouse_ray(context, event)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self.pressed = False
            ray = _mouse_ray(context, event)
            if self.dragging:
                pts = trace_rays(self.surface, [ray],
                                 anchor=self.stroke_pts[-1] if self.stroke_pts
                                 else None)
                if len(pts):
                    self.stroke_pts.append(pts[0])
                self._commit_traced(context, self.stroke_pts)
                self.stroke, self.stroke_pts = [], []
                self.dragging = False
            else:
                point = ray_surface(self.surface, *ray)
                if point is not None:
                    if self.anchor is None:
                        radius = _snap_radius(context, point)
                        self.anchor = resolve_anchor(self.graph, point, radius,
                                                     self.surface)[0]
                        refresh(active_object(context), self.graph, context,
                                rebuild=False)
                    else:
                        a_ray = self._ray_at(context, self.graph.nodes[self.anchor].co)
                        if a_ray is not None:
                            self._commit(context, interp_rays(a_ray, ray,
                                                              SEGMENT_SAMPLES))
            self._hover(context, event)
            return {"RUNNING_MODAL"}

        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            if self.anchor is not None:
                self.anchor = None
                overlay.clear_preview()
                return {"RUNNING_MODAL"}
            return self._finish(context, cancelled=True)

        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            return self._finish(context)

        return {"RUNNING_MODAL"}

    def _ray_at(self, context, co):
        from bpy_extras import view3d_utils
        region, rv3d = context.region, context.space_data.region_3d
        p2d = view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(tuple(co)))
        if p2d is None:
            return None
        return screen_ray(region, rv3d, (p2d.x, p2d.y))


# -- point tools ------------------------------------------------------------

def _arc_under(context, graph, event, surface):
    origin, direction = _mouse_ray(context, event)
    point = ray_surface(surface, origin, direction)
    if point is None:
        return None, None
    radius = _pick_radius(context, point)
    return point, nearest_on_arc(graph, point, radius)


class NXLOOM_OT_erase(bpy.types.Operator):
    """Delete the arc under the cursor, or dissolve the node under it"""

    bl_idname = "nxloom.erase"
    bl_label = "Erase Arc"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _context_ok(context)

    def invoke(self, context, event):
        obj = active_object(context)
        graph = get_graph(obj)
        surface = _surface_of(graph, context)
        if surface is None:
            return {"CANCELLED"}
        point, hit = _arc_under(context, graph, event, surface)
        if point is None:
            return {"CANCELLED"}

        node = nearest_node(graph, point, _pick_radius(context, point))
        if node is not None and dissolve_node(graph, node[0], surface) is not None:
            refresh(obj, graph, context)
            self.report({"INFO"}, "Node dissolved")
            return {"FINISHED"}
        if hit is None:
            return {"CANCELLED"}
        remove_arc(graph, hit[0])
        refresh(obj, graph, context)
        self.report({"INFO"}, f"Arc removed — {len(graph.patches)} patches")
        return {"FINISHED"}


class NXLOOM_OT_move_node(bpy.types.Operator):
    """Drag the layout node under the cursor along the surface"""

    bl_idname = "nxloom.move_node"
    bl_label = "Move Node"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return _context_ok(context)

    def invoke(self, context, event):
        obj = active_object(context)
        self.graph = get_graph(obj)
        self.surface = _surface_of(self.graph, context)
        if self.surface is None:
            return {"CANCELLED"}
        origin, direction = _mouse_ray(context, event)
        point = ray_surface(self.surface, origin, direction)
        if point is None:
            return {"CANCELLED"}
        hit = nearest_node(self.graph, point, _pick_radius(context, point))
        if hit is None:
            self.report({"WARNING"},
                        "No layout node under the cursor — raise Pick in the "
                        "Display panel if nodes are hard to grab")
            return {"CANCELLED"}
        self.nid = hit[0]
        self.start = np.array(self.graph.nodes[self.nid].co, dtype=float)
        context.window.cursor_modal_set("SCROLL_XY")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "MOUSEMOVE":
            origin, direction = _mouse_ray(context, event)
            point = ray_surface(self.surface, origin, direction)
            if point is not None:
                move_node(self.graph, self.nid, point, self.surface,
                          falloff=context.scene.nx_loom.node_falloff)
                overlay.set_preview(snap=point)
                set_graph(active_object(context), self.graph)
                overlay.mark_dirty()
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            context.window.cursor_modal_restore()
            overlay.clear_preview()
            refresh(active_object(context), self.graph, context)
            return {"FINISHED"}
        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            move_node(self.graph, self.nid, self.start, self.surface,
                      falloff=context.scene.nx_loom.node_falloff)
            set_graph(active_object(context), self.graph)
            context.window.cursor_modal_restore()
            overlay.clear_preview()
            refresh(active_object(context), self.graph, context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}


class NXLOOM_OT_set_arc_type(bpy.types.Operator):
    """Give the arc under the cursor the current arc type"""

    bl_idname = "nxloom.set_arc_type"
    bl_label = "Set Arc Type"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _context_ok(context)

    def invoke(self, context, event):
        obj = active_object(context)
        graph = get_graph(obj)
        surface = _surface_of(graph, context)
        if surface is None:
            return {"CANCELLED"}
        _, hit = _arc_under(context, graph, event, surface)
        if hit is None:
            return {"CANCELLED"}
        graph.arcs[hit[0]].type = context.scene.nx_loom.arc_type
        set_graph(obj, graph)
        overlay.mark_dirty()
        self.report({"INFO"}, f"Arc {hit[0]} -> {context.scene.nx_loom.arc_type}")
        return {"FINISHED"}


_LAST_HOVER_XY = [None]


class NXLOOM_OT_hover(bpy.types.Operator):
    """Highlight the node or arc under the cursor"""

    bl_idname = "nxloom.hover"
    bl_label = "Loom Hover"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return _context_ok(context)

    def invoke(self, context, event):
        xy = (event.mouse_region_x, event.mouse_region_y)
        last = _LAST_HOVER_XY[0]
        if last is not None and abs(xy[0] - last[0]) < 2 and abs(xy[1] - last[1]) < 2:
            return {"PASS_THROUGH"}
        _LAST_HOVER_XY[0] = xy

        obj = active_object(context)
        graph = get_graph(obj)
        surface = _surface_of(graph, context) if graph is not None else None
        if surface is None:
            overlay.clear_hover()
            return {"PASS_THROUGH"}

        origin, direction = _mouse_ray(context, event)
        point = ray_surface(surface, origin, direction)
        if point is None:
            overlay.clear_hover()
            return {"PASS_THROUGH"}

        radius = _pick_radius(context, point)
        node = nearest_node(graph, point, radius)
        if node is not None:
            overlay.set_hover(node=np.asarray(graph.nodes[node[0]].co, dtype=float))
            return {"PASS_THROUGH"}
        hit = nearest_on_arc(graph, point, radius)
        if hit is not None:
            overlay.set_hover(arc=np.asarray(graph.arcs[hit[0]].path, dtype=float))
            return {"PASS_THROUGH"}
        overlay.clear_hover()
        return {"PASS_THROUGH"}


class NXLOOM_OT_toggle_hole(bpy.types.Operator):
    """Mark the patch under the cursor as a hole, or fill it again"""

    bl_idname = "nxloom.toggle_hole"
    bl_label = "Toggle Hole"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _context_ok(context)

    def invoke(self, context, event):
        obj = active_object(context)
        graph = get_graph(obj)
        if graph is None or not graph.patches:
            return {"CANCELLED"}

        origin, direction = _mouse_ray(context, event)
        mw_inv = obj.matrix_world.inverted()
        lo = mw_inv @ Vector(tuple(origin))
        ld = (mw_inv.to_3x3() @ Vector(tuple(direction))).normalized()

        pid = None
        hit, _, _, face = obj.ray_cast(lo, ld)
        if hit and face >= 0:
            attr = obj.data.attributes.get("nx_loom_patch")
            if attr is not None and face < len(attr.data):
                pid = int(attr.data[face].value)

        if pid is None:
            # No generated face under the cursor: either an existing hole or the
            # background region. Pick whichever unfilled patch we are pointing
            # closest to, so a hole can be clicked back on.
            surface = _surface_of(graph, context)
            point = ray_surface(surface, origin, direction) if surface else None
            if point is None:
                self.report({"WARNING"}, "Nothing under the cursor")
                return {"CANCELLED"}
            best = None
            for cand in graph.patches:
                pts = graph.patch_boundary(cand)
                if not len(pts):
                    continue
                d = float(np.linalg.norm(pts.mean(axis=0) - point))
                if best is None or d < best[1]:
                    best = (cand, d)
            if best is None:
                return {"CANCELLED"}
            pid = best[0]

        now_hole = graph.patches[pid].fill != "hole"
        graph.set_hole(pid, now_hole)
        set_graph(obj, graph)
        refresh(obj, graph, context)
        self.report({"INFO"},
                    f"Patch {pid} is {'a hole' if now_hole else 'filled'}")
        return {"FINISHED"}


_CLASSES = (
    NXLOOM_OT_draw_arc,
    NXLOOM_OT_hover,
    NXLOOM_OT_toggle_hole,
    NXLOOM_OT_erase,
    NXLOOM_OT_move_node,
    NXLOOM_OT_set_arc_type,
)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
