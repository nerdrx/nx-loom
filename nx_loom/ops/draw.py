"""The drawing tools.

A modal shell over :mod:`nx_loom.core.authoring` and :mod:`nx_loom.core.picking`.
Everything that can be wrong lives in those two modules and is tested without a
viewport; what is left here is mouse plumbing.
"""

from __future__ import annotations

import bpy
import numpy as np
from mathutils import Vector

from ..core.authoring import (add_arc, decimate, dissolve_node, fair_path,
                              find_crossings, merge_nodes, move_node,
                              nearest_node, nearest_on_arc, new_node,
                              plane_snap, remove_arc, remove_node,
                              resolve_anchor)
from ..core.graph import GRAPH_KEY
from ..core.picking import (interp_rays, pixel_radius_world, ray_surface,
                            screen_ray, trace_rays)
from ..core.surface import Surface, cached_surface
from ..ui import overlay
from .layout import (active_object, get_graph, peek_graph,
                     rebuild_object, set_graph)

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


def _seam_plane(context, point):
    """(axis_index, world snap reach) at this point, or None without symmetry."""
    st = context.scene.nx_loom
    if st.symmetry_axis == "NONE" or point is None \
            or not getattr(st, "seam_snap", True):
        return None
    if getattr(context, "region", None) is None \
            or getattr(getattr(context, "space_data", None),
                       "region_3d", None) is None:
        # no viewport, no pixels — callers with a geometric scale build
        # their own plane
        return None
    from ..core.symmetry import AXIS_INDEX
    reach = pixel_radius_world(context.region, context.space_data.region_3d,
                               point, st.snap_pixels)
    return (AXIS_INDEX[st.symmetry_axis], reach)


def _pick_radius(context, point):
    """Grabbing something should be more forgiving than snapping to it."""
    return pixel_radius_world(context.region, context.space_data.region_3d,
                              point, context.scene.nx_loom.pick_pixels)


def commit_arc(graph, surface, rays, snap_radius, min_step,
               arc_type="flow", start_node=None, rail="surface", plane=None):
    """Trace rays onto the surface and add the resulting arc.

    Returns (arc_id, start_node, end_node), or None when the stroke produced
    nothing usable — off-surface, too short, or a loop back onto its own start.
    """
    path = trace_rays(surface, rays, min_step=min_step * 0.25)
    return commit_path(graph, surface, path, snap_radius, min_step,
                       arc_type, start_node, rail, plane=plane)


def commit_path(graph, surface, path, snap_radius, min_step,
                arc_type="flow", start_node=None, rail="surface",
                smooth=0.0, plane=None):
    """Add an arc from an already-traced surface path.

    ``smooth`` fairs hand jitter out of freehand strokes before the arc is
    stored — a wobbly arc otherwise becomes a wobbly edge loop in every mesh
    generated from it, forever. Straight rails never smooth: they have no
    stroke to be jittery.
    """
    path = np.asarray(path, dtype=float)
    if len(path) < 2:
        return None

    # A stroke that attaches to the mirrored half is redirected wholesale:
    # reflect it across the plane and commit on the authored side, and sync
    # mirrors it back to where the hand drew it. Committing against derived
    # geometry directly would split derived arcs into orphaned authored
    # stubs that fight the next sync.
    if plane is not None and start_node is None:
        ax = plane[0]

        def _derived_at(pt):
            hit = nearest_node(graph, pt, snap_radius)
            if hit is not None:
                return graph.nodes[hit[0]].mirror_of is not None
            hit = nearest_on_arc(graph, pt, snap_radius)
            if hit is not None:
                return graph.arcs[hit[0]].mirror_of is not None
            return False

        if _derived_at(path[0]) or _derived_at(path[-1]):
            path = np.asarray(path, dtype=float).copy()
            path[:, ax] *= -1.0
            if surface is not None:
                path = np.asarray(surface.project(path), dtype=float)

    a = start_node
    if a is None:
        a = resolve_anchor(graph, path[0], snap_radius, surface, plane)[0]
    b = resolve_anchor(graph, path[-1], snap_radius, surface, plane)[0]
    if a == b:
        return None
    path = decimate(path, min_step)
    if len(path) < 2:
        path = np.vstack([graph.nodes[a].co, graph.nodes[b].co])
    if rail == "surface" and smooth > 0.0 and len(path) > 3:
        project = surface.project if surface is not None else None
        path = fair_path(path, iters=max(int(round(smooth * 24)), 1),
                         strength=0.5, project=project)

    # A line drawn THROUGH an existing arc must connect to it, exactly as a
    # line ending ON one does. Without the shared junction the layout is
    # non-planar and patch discovery silently mis-traverses it. Each crossing
    # splits the existing arc (resolve_anchor's job) and cuts this path, so
    # both routes meet at one node.
    crossings = find_crossings(graph, path, max(min_step, snap_radius * 0.5))
    prev_node, prev_idx = a, 0
    made = []
    for _dist, k, pt in crossings:
        nid = resolve_anchor(graph, pt, snap_radius, surface)[0]
        if nid == prev_node or nid == b:
            continue
        sub = path[prev_idx:k + 1]
        if len(sub) < 2:
            sub = np.vstack([graph.nodes[prev_node].co, graph.nodes[nid].co])
        made.append(add_arc(graph, prev_node, nid, sub, surface,
                            type=arc_type, rail=rail))
        prev_node, prev_idx = nid, k
    sub = path[prev_idx:]
    if len(sub) < 2:
        sub = np.vstack([graph.nodes[prev_node].co, graph.nodes[b].co])
    aid = add_arc(graph, prev_node, b, sub, surface, type=arc_type, rail=rail)
    return aid, a, b


_SEAM_CACHE = {}


def _update_seam_curve(obj, surface, st):
    """The mirror plane's trace on the reference, for the overlay.

    Recomputed only when the reference or axis changes — a cross-section over
    an avatar-scale mesh is not per-edit work.
    """
    if surface is None or st.symmetry_axis == "NONE":
        obj.pop("nx_loom_seam_curve", None)
        return
    key = (getattr(surface, "token", None), st.symmetry_axis)
    hit = _SEAM_CACHE.get("s")
    if hit is None or hit[0] != key:
        from ..core.contour import cross_section
        from ..core.symmetry import AXIS_INDEX
        n = [0.0, 0.0, 0.0]
        n[AXIS_INDEX[st.symmetry_axis]] = 1.0
        loops = cross_section(surface.verts, surface.tris, (0, 0, 0), n)
        flat = []
        for loop in loops:
            step = max(len(loop) // 160, 1)
            for q in loop[::step]:
                flat.extend([float(q[0]), float(q[1]), float(q[2])])
            flat.extend([float("nan")] * 3)   # break between loops
        _SEAM_CACHE["s"] = (key, flat)
        hit = _SEAM_CACHE["s"]
    obj["nx_loom_seam_curve"] = hit[1]


def refresh(obj, graph, context, rebuild=True):
    """Re-derive patches, optionally regenerate, and record what failed."""
    from ..core.budget import drain
    return drain(refresh_iter(obj, graph, context, rebuild=rebuild))


def refresh_iter(obj, graph, context, rebuild=True):
    """refresh() as a progress generator, pumpable by a background job."""
    from ..core import symmetry as sym
    from ..core.budget import OutOfTime
    from .layout import rebuild_iter
    st = context.scene.nx_loom
    surface = _surface_of(graph, context)
    normal_at = surface.normal_at if surface else None
    yield (0.02, "syncing the layout")
    sym.sync(graph, st.symmetry_axis, st.symmetry_tolerance, surface)
    drep = graph.discover_patches(normal_at=normal_at,
                                  corner_angle=st.corner_angle)
    sym.enforce_mirrored_patches(graph, st.symmetry_axis,
                                 st.symmetry_tolerance)
    _update_seam_curve(obj, surface, st)
    obj["nx_loom_bigons"] = int(drep.get("rejected", {}).get("bigon", 0))
    set_graph(obj, graph)
    bad = []
    if rebuild and graph.patches:
        gen = rebuild_iter(obj, context)
        rep = None
        try:
            while True:
                try:
                    item = next(gen)
                except StopIteration as stop:
                    rep = stop.value
                    break
                if item is not None:
                    yield (0.04 + 0.92 * float(item[0]), item[1])
        except OutOfTime as exc:
            # Old mesh and warnings stay as they were; the panel names why.
            obj["nx_loom_timeout"] = str(exc)
            print(f"NX Loom: {exc}")
            overlay.mark_dirty()
            return list(obj.get("nx_loom_bad_patches", []) or [])
        if rep:
            obj["nx_loom_lock_conflicts"] = len(rep.get("lock_conflicts", []))
            bad = sorted(set(rep["unsatisfied_patches"])
                         | {pid for pid, why in rep["failed_patches"]
                            if why != "background"})
            obj["nx_loom_background"] = list(rep.get("background", []))
            from ..core.diagnose import diagnose, plan_fixes
            diag = diagnose(graph, rep)
            obj["nx_loom_diag"] = {str(k): list(v) for k, v in diag.items()}
            # ID properties refuse mixed-type arrays; a dict of dicts it is
            obj["nx_loom_fixes"] = {
                str(i): {"label": str(l), "kind": str(k2), "payload": int(pl)}
                for i, (l, k2, pl) in enumerate(plan_fixes(graph, rep))}
    obj["nx_loom_bad_patches"] = bad
    if not bad:
        obj["nx_loom_diag"] = {}
        obj["nx_loom_fixes"] = {}
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
            "Click: chain segment   Drag: freehand   Ctrl+Click: no seam snap"
            "   Esc/RMB: end chain, again to finish   Enter: finish"
        )

    def _surface_point(self, context, event):
        origin, direction = _mouse_ray(context, event)
        return ray_surface(self.surface, origin, direction), (origin, direction)

    def _snap_preview(self, context, point):
        if point is None:
            overlay.set_seam(None)
            return None
        snapped, on_seam = plane_snap(point, _seam_plane(context, point),
                                      self.surface)
        overlay.set_seam(snapped if on_seam else None)
        r = _snap_radius(context, point)
        hit = nearest_node(self.graph, point, r)
        if hit is not None:
            return self.graph.nodes[hit[0]].co
        hit = nearest_on_arc(self.graph, point, r)
        if hit is not None:
            return hit[3]
        return snapped if on_seam else None

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
                          start_node=self.anchor, rail="surface",
                          smooth=context.scene.nx_loom.stroke_smooth,
                          plane=_seam_plane(context, path[-1] if len(path) else None))
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
        probe = trace_rays(self.surface, rays[-1:])
        end = probe[0] if len(probe) else None
        plane = None if getattr(self, "_ctrl_click", False) \
            else _seam_plane(context, end)
        res = commit_arc(self.graph, self.surface, rays, radius, self.min_step,
                         arc_type=context.scene.nx_loom.arc_type,
                         start_node=self.anchor, rail="straight",
                         plane=plane)
        return self._after_commit(context, res)

    def _after_commit(self, context, res):
        if res is None:
            return False
        obj = active_object(context)
        _, _, end = res
        self.anchor = end
        self.made += 1
        self.touched = True
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
        overlay.set_seam(None)
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)
        obj = active_object(context)
        if self.made:
            refresh(obj, self.graph, context, rebuild=True)
        if self.touched:
            # Placed points count as edits even with no arc drawn yet — without
            # this push they were saved but not undoable.
            bpy.ops.ed.undo_push(message=f"NX Loom: draw ({self.made} arc(s))")
            self.report({"INFO"}, f"{self.made} arc(s), "
                                  f"{len(self.graph.patches)} patches")
        return {"CANCELLED"} if cancelled and not self.touched else {"FINISHED"}

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
            self._ctrl_click = bool(event.ctrl)
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
                        plane = None if event.ctrl \
                            else _seam_plane(context, point)
                        self.anchor = resolve_anchor(self.graph, point, radius,
                                                     self.surface, plane)[0]
                        self.touched = True
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
            self.report({"ERROR"}, "Set a Reference mesh first")
            return {"CANCELLED"}
        point, hit = _arc_under(context, graph, event, surface)
        if point is None:
            return {"CANCELLED"}

        from ..core.symmetry import source_arc, source_node
        node = nearest_node(graph, point, _pick_radius(context, point))
        if node is not None:
            # Erasing the mirrored half used to be a zombie hunt: the derived
            # copy came straight back on the next sync. Every destructive
            # gesture on derived geometry redirects to its authored source —
            # both halves go, and stay gone.
            nid = source_node(graph, node[0])
            valence = graph.valence().get(nid, 0)
            if valence == 2 and dissolve_node(graph, nid, surface) is not None:
                refresh(obj, graph, context)
                self.report({"INFO"}, "Node dissolved")
                return {"FINISHED"}
            if valence != 2:
                # A loose point or a junction: dissolving cannot apply, and
                # leaving the click dead strands the user with a point they
                # cannot get rid of. Deleting is what the gesture means here.
                n_arcs = remove_node(graph, nid)
                refresh(obj, graph, context)
                self.report({"INFO"},
                            "Point removed" if n_arcs == 0 else
                            f"Node and {n_arcs} arc(s) removed")
                return {"FINISHED"}
        if hit is None:
            self.report({"WARNING"}, "Nothing under the cursor to erase")
            return {"CANCELLED"}
        remove_arc(graph, source_arc(graph, hit[0]))
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
            self.report({"ERROR"}, "Set a Reference mesh first")
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
        # Dragging a mirrored node used to hold until some later edit
        # regenerated the mirror and silently reverted it. The drag drives the
        # authored source with the mirrored position instead — and moves the
        # grabbed copy too, so the hand still sees what it is doing.
        from ..core.symmetry import AXIS_INDEX, source_node
        self.nid = hit[0]
        self.src_nid = source_node(self.graph, self.nid)
        self.mirror_ax = None
        if self.src_nid != self.nid \
                and context.scene.nx_loom.symmetry_axis != "NONE":
            self.mirror_ax = AXIS_INDEX[context.scene.nx_loom.symmetry_axis]
        self.start = np.array(self.graph.nodes[self.nid].co, dtype=float)
        self.merge_target = None
        # frozen copies of the incident arcs: sliding must follow the lines
        # as they were when the drag began — the live paths deform under the
        # node as it moves, which would make the rail chase its own tail
        self.rails = [np.asarray(arc.path, dtype=float).copy()
                      for arc in self.graph.arcs.values()
                      if arc.a == self.nid or arc.b == self.nid]
        context.window.cursor_modal_set("SCROLL_XY")
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Drag along the surface — drop onto another node to merge, "
            "hold Ctrl to slide along the node's own arcs")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "MOUSEMOVE":
            origin, direction = _mouse_ray(context, event)
            point = ray_surface(self.surface, origin, direction)
            if point is not None and event.ctrl and self.rails:
                # slide: constrain to the nearest point on the node's own
                # arcs, as captured at drag start
                best = None
                for rail in self.rails:
                    a, ab = rail[:-1], rail[1:] - rail[:-1]
                    denom = np.einsum("ij,ij->i", ab, ab)
                    denom[denom < 1e-20] = 1e-20
                    t = np.clip(np.einsum("ij,ij->i", point - a, ab) / denom,
                                0.0, 1.0)
                    proj = a + ab * t[:, None]
                    d = np.linalg.norm(proj - point, axis=1)
                    i = int(np.argmin(d))
                    if best is None or d[i] < best[0]:
                        best = (float(d[i]), proj[i])
                if best is not None:
                    point = np.asarray(best[1], dtype=float)
            if point is not None:
                # Dropping onto another node welds them — armed whenever the
                # cursor is within pick range of one, shown as its highlight.
                # A merge target outranks the seam snap: welding is the more
                # specific intent.
                tgt = nearest_node(self.graph, point,
                                   _pick_radius(context, point),
                                   skip=(self.nid,))
                self.merge_target = tgt[0] if tgt else None
                if self.merge_target is not None:
                    tco = np.asarray(
                        self.graph.nodes[self.merge_target].co, dtype=float)
                    overlay.set_hover(node=tco)
                    overlay.set_seam(None)
                    point = tco
                else:
                    overlay.set_hover()
                    point, on_seam = plane_snap(point,
                                                _seam_plane(context, point),
                                                self.surface)
                    overlay.set_seam(point if on_seam else None)
                move_node(self.graph, self.nid, point, self.surface,
                          falloff=context.scene.nx_loom.node_falloff)
                if self.mirror_ax is not None:
                    reflected = np.asarray(point, dtype=float).copy()
                    reflected[self.mirror_ax] *= -1.0
                    move_node(self.graph, self.src_nid, reflected,
                              self.surface,
                              falloff=context.scene.nx_loom.node_falloff)
                overlay.set_preview(snap=point)
                set_graph(active_object(context), self.graph)
                overlay.mark_dirty()
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            context.window.cursor_modal_restore()
            context.workspace.status_text_set(None)
            overlay.clear_preview()
            overlay.clear_hover()
            overlay.set_seam(None)
            if self.merge_target is not None:
                from ..core.symmetry import source_node
                a = source_node(self.graph, self.nid)
                b = source_node(self.graph, self.merge_target)
                collapsed = merge_nodes(
                    self.graph, a, b, self.surface,
                    falloff=context.scene.nx_loom.node_falloff) \
                    if a != b else None
                if collapsed is not None:
                    self.report({"INFO"},
                                "Nodes merged"
                                + (f", {collapsed} connecting arc(s) collapsed"
                                   if collapsed else ""))
            refresh(active_object(context), self.graph, context)
            return {"FINISHED"}
        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            move_node(self.graph, self.nid, self.start, self.surface,
                      falloff=context.scene.nx_loom.node_falloff)
            if self.mirror_ax is not None:
                reflected = self.start.copy()
                reflected[self.mirror_ax] *= -1.0
                move_node(self.graph, self.src_nid, reflected, self.surface,
                          falloff=context.scene.nx_loom.node_falloff)
            set_graph(active_object(context), self.graph)
            context.window.cursor_modal_restore()
            context.workspace.status_text_set(None)
            overlay.clear_hover()
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
            self.report({"ERROR"}, "Set a Reference mesh first")
            return {"CANCELLED"}
        _, hit = _arc_under(context, graph, event, surface)
        if hit is None:
            self.report({"WARNING"}, "No arc under the cursor")
            return {"CANCELLED"}
        from ..core.symmetry import source_arc
        src_id = source_arc(graph, hit[0])
        graph.arcs[src_id].type = context.scene.nx_loom.arc_type
        graph.arcs[hit[0]].type = context.scene.nx_loom.arc_type
        set_graph(obj, graph)
        overlay.mark_dirty()
        self.report({"INFO"}, f"Arc {hit[0]} -> {context.scene.nx_loom.arc_type}")
        return {"FINISHED"}


_LAST_HOVER_XY = [None]
ACTIVE_KEY = "nx_loom_active_arc"
_PENDING = {"obj": None}


def active_arc(obj):
    aid = obj.get(ACTIVE_KEY) if obj is not None else None
    return None if aid is None else int(aid)


def set_active_arc(obj, aid):
    if aid is None:
        obj.pop(ACTIVE_KEY, None)
    else:
        obj[ACTIVE_KEY] = int(aid)
    overlay.mark_dirty()


def _sync_active_loops(context, graph, aid):
    """Show the selected arc's count in the panel without re-applying it."""
    st = context.scene.nx_loom
    arc = graph.arcs.get(aid) if graph else None
    if arc is None or arc.n is None:
        return
    if int(st.active_loops) != int(arc.n):
        st["active_loops"] = int(arc.n)      # bypass the update callback


def _deferred_rebuild():
    """Coalesce a burst of adjustments into one rebuild.

    A wheel notch used to rebuild the mesh immediately, so on a heavy layout
    the events queued up behind the rebuilds and the count sailed past whatever
    was wanted. Now the pin lands at once and the mesh catches up when the
    wheel stops.
    """
    name = _PENDING["obj"]
    _PENDING["obj"] = None
    obj = bpy.data.objects.get(name) if name else None
    if obj is None:
        # deleted before the timer fired — nothing to rebuild, nothing to leak
        return None
    try:
        ctx = bpy.context
        graph = get_graph(obj)
        if graph is not None:
            from . import jobs
            last_ms = float(obj.get("nx_loom_build_ms", 0.0) or 0.0)
            if jobs.should_run_async(ctx, last_ms):
                name = obj.name

                def _done(_bad, why, _name=name):
                    o = bpy.data.objects.get(_name)
                    if o is not None and why is None:
                        _sync_active_loops(bpy.context, get_graph(o),
                                           active_arc(o))
                jobs.start("Rebuild", refresh_iter(obj, graph, ctx),
                           on_done=_done,
                           budget=ctx.scene.nx_loom.job_budget or None)
            else:
                refresh(obj, graph, ctx)
                _sync_active_loops(ctx, get_graph(obj), active_arc(obj))
    except Exception:
        # Never silently: if the deferred rebuild dies, the pin is stored and
        # nothing re-solves, which looks exactly like the solver ignoring it.
        import traceback
        traceback.print_exc()
    return None


def queue_rebuild(obj, delay=0.25):
    """Coalesce; holds the NAME, never the object — a bpy reference kept
    across frames dies with a ReferenceError if the object is deleted."""
    first = _PENDING["obj"] is None
    _PENDING["obj"] = obj.name
    if first:
        bpy.app.timers.register(_deferred_rebuild, first_interval=delay)


def apply_active_loops(context, want):
    """Pin the selected arc to an exact count, typed rather than scrolled."""
    obj = active_object(context)
    if obj is None or GRAPH_KEY not in obj:
        return
    aid = active_arc(obj)
    graph = get_graph(obj)
    if graph is None or aid is None or aid not in graph.arcs:
        return
    if graph.arcs[aid].n_lock == want:
        return
    before = set(obj.get("nx_loom_bad_patches", []) or [])
    graph.arcs[aid].n_lock = max(1, int(want))
    set_graph(obj, graph)
    bad = refresh(obj, graph, context)
    newly = set(bad or []) - before
    obj["nx_loom_pin_warn"] = (
        f"the {want}* pin cannot hold — {len(newly)} patch(es) went "
        f"unresolved (Fix It can release the loser)" if newly else "")


class NXLOOM_OT_select_arc(bpy.types.Operator):
    """Select the arc under the cursor so its loop count can be typed"""

    bl_idname = "nxloom.select_arc"
    bl_label = "Select Arc"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _context_ok(context)

    def invoke(self, context, event):
        obj = active_object(context)
        graph = get_graph(obj)
        surface = _surface_of(graph, context) if graph is not None else None
        if surface is None:
            self.report({"ERROR"}, "Set a Reference mesh first")
            return {"CANCELLED"}
        _, hit = _arc_under(context, graph, event, surface)
        if hit is None:
            set_active_arc(obj, None)
            self.report({"INFO"}, "Nothing selected")
            return {"CANCELLED"}
        from ..core.symmetry import source_arc
        set_active_arc(obj, source_arc(graph, hit[0]))
        hit = (source_arc(graph, hit[0]),) + tuple(hit[1:])
        _sync_active_loops(context, graph, hit[0])
        arc = graph.arcs[hit[0]]
        self.report({"INFO"}, f"Arc {hit[0]}: {arc.n} loops"
                              + (" (pinned)" if arc.n_lock else ""))
        return {"FINISHED"}


class NXLOOM_OT_unpin_arc(bpy.types.Operator):
    """Unpin just the selected arc"""

    bl_idname = "nxloom.unpin_arc"
    bl_label = "Unpin Arc"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        if obj is None or GRAPH_KEY not in obj:
            return False
        aid = active_arc(obj)
        graph = get_graph(obj)
        return bool(graph and aid is not None and aid in graph.arcs
                    and graph.arcs[aid].n_lock)

    def execute(self, context):
        obj = active_object(context)
        graph = get_graph(obj)
        aid = active_arc(obj)
        graph.arcs[aid].n_lock = None
        set_graph(obj, graph)
        refresh(obj, graph, context)
        _sync_active_loops(context, get_graph(obj), aid)
        self.report({"INFO"}, f"Arc {aid} unpinned")
        return {"FINISHED"}


def bridge_rings(graph, surface, ring_a, ring_b, arc_type="flow"):
    """Connect two rings' corresponding nodes with straight wall arcs.

    Straight-rail, like a click-click segment: a wall arc has no shape of its
    own, so a moved ring node re-lays it end to end. Returns the arc ids, or
    None when the rings do not pair or are too far apart to be one tube —
    ringing the left leg and then the right must never span the gap.
    """
    from ..core.contour import bridgeable, pair_rings

    a_pts = [np.asarray(graph.nodes[n].co, dtype=float) for n in ring_a]
    b_pts = [np.asarray(graph.nodes[n].co, dtype=float) for n in ring_b]
    pairs = pair_rings(a_pts, b_pts)
    if not bridgeable(a_pts, b_pts, pairs):
        return None
    made = []
    for i, j in pairs:
        t = np.linspace(0.0, 1.0, 10)[:, None]
        path = a_pts[i] * (1.0 - t) + b_pts[j] * t
        if surface is not None:
            path[1:-1] = surface.project(path[1:-1])
        made.append(add_arc(graph, ring_a[i], ring_b[j], path, surface,
                            type=arc_type, rail="straight"))
    return made


def ring_from_plane(graph, surface, plane_pt, plane_n, near, anchor,
                    arc_type="flow", k=4):
    """Cut the surface with a plane and emit the loop nearest ``near`` as a
    ring anchored at ``anchor``. The plane can come from a swipe or be
    extrapolated from the last two — the geometry does not care."""
    from ..core.contour import cross_section, nearest_loop, ring_segments

    loops = cross_section(surface.verts, surface.tris, plane_pt, plane_n)
    loop = nearest_loop(loops, near)
    if loop is None:
        return None
    res = ring_segments(loop, k=k, start_at=anchor)
    if res is None:
        return None
    nodes_pts, paths = res
    node_ids = [new_node(graph, pt, surface) for pt in nodes_pts]
    arc_ids = [add_arc(graph, node_ids[j], node_ids[(j + 1) % k], paths[j],
                       surface, type=arc_type, rail="surface")
               for j in range(k)]
    return node_ids, arc_ids


def commit_ring(graph, surface, ray0, ray1, arc_type="flow", k=4,
                bridge_to=None):
    """Turn a swipe across a limb into a closed ring of k arcs.

    The stroke's two surface hits and the view direction span the cutting
    plane; the plane's cross-section with the reference is chained into loops
    and the loop nearest the stroke is the one the artist meant — a swipe on
    the leg must never grab the torso loop behind it. Returns
    (node_ids, arc_ids) or None with nothing changed.
    """
    from ..core.contour import cross_section, nearest_loop, ring_segments

    # A natural swipe overshoots the silhouette on both ends — the pointer
    # crosses the whole limb and keeps going. Sample along the stroke and keep
    # the part that actually lands, instead of requiring both endpoints to hit.
    pts = trace_rays(surface, interp_rays(ray0, ray1, 16))
    if len(pts) < 2:
        return None
    p0, p1 = pts[0], pts[-1]
    d = p1 - p0
    if float(np.linalg.norm(d)) < 1e-9:
        return None
    view = np.asarray(ray0[1], dtype=float) + np.asarray(ray1[1], dtype=float)
    normal = np.cross(d, view)
    if float(np.linalg.norm(normal)) < 1e-9:
        return None

    mid = (p0 + p1) * 0.5
    made = ring_from_plane(graph, surface, mid, normal, mid, p0,
                           arc_type=arc_type, k=k)
    if made is None:
        return None
    node_ids, arc_ids = made

    bridged = None
    if bridge_to and all(n in graph.nodes for n in bridge_to) \
            and len(bridge_to) == k:
        bridged = bridge_rings(graph, surface, list(bridge_to), node_ids,
                               arc_type=arc_type)
    return node_ids, arc_ids, bridged


class NXLOOM_OT_ring_cut(bpy.types.Operator):
    """Swipe across a limb to ring it with a closed loop.

    One gesture replaces clicking around the back of the mesh with the view
    rotated — the cutting plane finds the far side for you.
    """

    bl_idname = "nxloom.ring_cut"
    bl_label = "Ring Cut"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return _context_ok(context)

    def invoke(self, context, event):
        obj = active_object(context)
        self.graph = get_graph(obj)
        self.surface = _surface_of(self.graph, context) if self.graph else None
        if self.surface is None:
            self.report({"ERROR"}, "Set a Reference mesh first")
            return {"CANCELLED"}
        self.ray0 = _mouse_ray(context, event)
        if ray_surface(self.surface, *self.ray0) is None:
            self.report({"WARNING"}, "Start the swipe on the surface")
            return {"CANCELLED"}
        context.window.cursor_modal_set("CROSSHAIR")
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Swipe across the limb and release — Esc cancels")
        return {"RUNNING_MODAL"}

    def _cleanup(self, context):
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)
        overlay.clear_preview()

    def modal(self, context, event):
        if event.type == "MOUSEMOVE":
            ray = _mouse_ray(context, event)
            p0 = ray_surface(self.surface, *self.ray0)
            p1 = ray_surface(self.surface, *ray)
            if p0 is not None and p1 is not None:
                overlay.set_preview(path=np.vstack([p0, p1]))
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._cleanup(context)
            st = context.scene.nx_loom
            obj = active_object(context)
            prev = list(obj.get("nx_loom_last_ring", []) or []) \
                if st.bridge_rings else None
            res = commit_ring(self.graph, self.surface, self.ray0,
                              _mouse_ray(context, event),
                              arc_type=st.arc_type, bridge_to=prev)
            if res is None:
                self.report({"WARNING"},
                            "No closed loop under the swipe — cross the limb "
                            "in one stroke")
                return {"CANCELLED"}
            node_ids, arc_ids, bridged = res
            obj["nx_loom_last_ring"] = [int(n) for n in node_ids]
            pts2 = trace_rays(self.surface,
                              interp_rays(self.ray0,
                                          _mouse_ray(context, event), 16))
            if len(pts2) >= 2:
                p0, p1 = pts2[0], pts2[-1]
                view = (np.asarray(self.ray0[1], dtype=float)
                        + np.asarray(_mouse_ray(context, event)[1],
                                     dtype=float))
                normal = np.cross(p1 - p0, view)
                hist = list(obj.get("nx_loom_ring_hist", []) or [])
                hist.append(list(map(float, list((p0 + p1) * 0.5)
                                     + list(normal) + list(p0))))
                obj["nx_loom_ring_hist"] = hist[-2:]
            refresh(obj, self.graph, context, rebuild=st.rebuild_on_draw)
            bpy.ops.ed.undo_push(message="NX Loom: ring cut")
            if bridged:
                self.report({"INFO"},
                            f"Ring bridged to the previous one — "
                            f"{len(bridged)} wall arcs")
            elif prev:
                self.report({"INFO"},
                            "Ring of 4 arcs (previous ring too far to bridge)")
            else:
                self.report({"INFO"}, "Ring of 4 arcs")
            return {"FINISHED"}

        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            self._cleanup(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}


def commit_halo(graph, surface, center_ray, edge_ray, arc_type="flow", k=4,
                bridge_to=None, samples=48):
    """A closed ring around a point — the eye-socket and mouth gesture.

    A circle in the tangent plane at the centre, projected onto the surface.
    For socket-scale radii the projection is faithful; a halo the size of the
    whole head is a job for ring cut, not this. The first node is anchored
    where the drag was released, so the artist chooses where the ring's
    corners sit — and two concentric halos bridge into an instant loop band.
    """
    from ..core.contour import ring_segments

    center = ray_surface(surface, *center_ray)
    edge = ray_surface(surface, *edge_ray)
    if center is None or edge is None:
        return None
    r = float(np.linalg.norm(edge - center))
    if r < 1e-6:
        return None

    n = surface.normal_at(center)
    n = n / max(np.linalg.norm(n), 1e-12)
    t = edge - center
    t = t - n * (t @ n)
    if float(np.linalg.norm(t)) < 1e-9:
        return None
    t /= np.linalg.norm(t)
    b = np.cross(n, t)

    ang = np.linspace(0.0, 2.0 * np.pi, samples, endpoint=False)
    circle = center + r * (np.cos(ang)[:, None] * t + np.sin(ang)[:, None] * b)
    circle = surface.project(circle)
    loop = np.vstack([circle, circle[:1]])

    res = ring_segments(loop, k=k, start_at=edge)
    if res is None:
        return None
    nodes_pts, paths = res
    node_ids = [new_node(graph, pt, surface) for pt in nodes_pts]
    arc_ids = [add_arc(graph, node_ids[j], node_ids[(j + 1) % k], paths[j],
                       surface, type=arc_type, rail="surface")
               for j in range(k)]

    bridged = None
    if bridge_to and all(nid in graph.nodes for nid in bridge_to) \
            and len(bridge_to) == k:
        bridged = bridge_rings(graph, surface, list(bridge_to), node_ids,
                               arc_type=arc_type)
    return node_ids, arc_ids, bridged


class NXLOOM_OT_halo(bpy.types.Operator):
    """Drag outward from a point to ring it — eye sockets, mouths, any opening"""

    bl_idname = "nxloom.halo"
    bl_label = "Halo"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return _context_ok(context)

    def invoke(self, context, event):
        obj = active_object(context)
        self.graph = get_graph(obj)
        self.surface = _surface_of(self.graph, context) if self.graph else None
        if self.surface is None:
            self.report({"ERROR"}, "Set a Reference mesh first")
            return {"CANCELLED"}
        self.center_ray = _mouse_ray(context, event)
        self.center = ray_surface(self.surface, *self.center_ray)
        if self.center is None:
            self.report({"WARNING"}, "Start on the surface")
            return {"CANCELLED"}
        context.window.cursor_modal_set("CROSSHAIR")
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Drag outward to size the halo, release to place — Esc cancels")
        return {"RUNNING_MODAL"}

    def _cleanup(self, context):
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)
        overlay.clear_preview()

    def _preview_circle(self, edge):
        r = float(np.linalg.norm(edge - self.center))
        if r < 1e-6:
            return None
        n = self.surface.normal_at(self.center)
        n = n / max(np.linalg.norm(n), 1e-12)
        t = edge - self.center
        t = t - n * (t @ n)
        if float(np.linalg.norm(t)) < 1e-9:
            return None
        t /= np.linalg.norm(t)
        b = np.cross(n, t)
        ang = np.linspace(0.0, 2.0 * np.pi, 33)
        ring = self.center + r * (np.cos(ang)[:, None] * t
                                  + np.sin(ang)[:, None] * b)
        return self.surface.project(ring)

    def modal(self, context, event):
        if event.type == "MOUSEMOVE":
            edge = ray_surface(self.surface, *_mouse_ray(context, event))
            if edge is not None:
                path = self._preview_circle(edge)
                if path is not None:
                    overlay.set_preview(path=path, anchor=self.center)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._cleanup(context)
            st = context.scene.nx_loom
            obj = active_object(context)
            prev = list(obj.get("nx_loom_last_ring", []) or []) \
                if st.bridge_rings else None
            res = commit_halo(self.graph, self.surface, self.center_ray,
                              _mouse_ray(context, event),
                              arc_type=st.arc_type, bridge_to=prev)
            if res is None:
                self.report({"WARNING"}, "Drag outward on the surface to size "
                                         "the halo")
                return {"CANCELLED"}
            node_ids, _, bridged = res
            obj["nx_loom_last_ring"] = [int(n) for n in node_ids]
            refresh(obj, self.graph, context, rebuild=st.rebuild_on_draw)
            bpy.ops.ed.undo_push(message="NX Loom: halo")
            self.report({"INFO"}, "Halo bridged to the previous ring"
                        if bridged else "Halo of 4 arcs")
            return {"FINISHED"}

        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            self._cleanup(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}


class NXLOOM_OT_symmetrize_side(bpy.types.Operator):
    """Make the layout truly mirrored: keep one side's unpaired arcs, drop the
    other side's, and regenerate real mirrors for them"""

    bl_idname = "nxloom.symmetrize_side"
    bl_label = "Make Truly Mirrored"
    bl_options = {"REGISTER", "UNDO"}

    keep: bpy.props.EnumProperty(
        name="Keep",
        items=[("POS", "Keep + Side", "Authored arcs on the positive side win"),
               ("NEG", "Keep − Side", "Authored arcs on the negative side win"),
               ("ACTIVE", "Keep Selected Side",
                "The side holding the selected arc wins — Alt+Shift click an "
                "arc on the side you like first")],
        default="ACTIVE",
    )
    scope: bpy.props.EnumProperty(
        name="Scope",
        items=[("LOOSE", "Unpaired Only",
                "Fix only arcs with no partner. Twinned pairs keep both "
                "hand-drawn shapes, with their counts already tied"),
               ("ALL", "Exact Mirror",
                "Also replace twinned counterparts with exact mirrors of the "
                "kept side — geometric symmetry, discarding the other side's "
                "hand-drawn shapes")],
        default="LOOSE",
    )

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj
                    and context.scene.nx_loom.symmetry_axis != "NONE")

    def execute(self, context):
        from ..core import symmetry as sym

        obj = active_object(context)
        st = context.scene.nx_loom
        graph = get_graph(obj)
        surface = _surface_of(graph, context)
        ax = sym.AXIS_INDEX[st.symmetry_axis]

        loose = sym.unpaired_arcs(graph, st.symmetry_axis,
                                  st.symmetry_tolerance)
        mism = sym.mismatched_twins(graph, st.symmetry_axis) \
            if self.scope == "ALL" else []
        if not loose and not mism:
            self.report({"INFO"}, "Every arc is already paired"
                        + ("" if self.scope == "ALL"
                           else " — use Exact Mirror if the shapes differ"))
            return {"CANCELLED"}

        if self.keep == "ACTIVE":
            aid = active_arc(obj)
            if aid is None or aid not in graph.arcs:
                self.report({"ERROR"},
                            "Alt+Shift click an arc on the side you want to "
                            "keep, then run this again")
                return {"CANCELLED"}
            side = float(np.asarray(graph.arcs[aid].path)[:, ax].mean())
            if abs(side) <= st.symmetry_tolerance:
                self.report({"ERROR"},
                            "The selected arc sits on the seam — select one "
                            "clearly on the side you want to keep")
                return {"CANCELLED"}
            want_sign = 1.0 if side > 0 else -1.0
        else:
            want_sign = 1.0 if self.keep == "POS" else -1.0

        def discard_side(aid):
            return float(np.asarray(graph.arcs[aid].path)[:, ax].mean()) \
                * want_sign < 0

        doomed = [aid for aid in loose if discard_side(aid)]
        if self.scope == "ALL":
            # Twins tie counts but keep both hand-drawn shapes. Exact Mirror
            # discards the other side's shapes so true mirrors regenerate.
            twin_targets = {a.twin for a in graph.arcs.values()
                            if a.twin is not None}
            for aid, arc in list(graph.arcs.items()):
                if (arc.twin is not None or aid in twin_targets) \
                        and discard_side(aid):
                    doomed.append(aid)
        for aid in set(doomed):
            remove_arc(graph, aid)
        for arc in graph.arcs.values():
            if arc.twin is not None and arc.twin not in graph.arcs:
                arc.twin = None
        # force a full resync: the kept side's unpaired arcs now have no
        # covering geometry in the way, so true mirrors regenerate for them
        graph.settings.pop("sym_sig", None)

        set_graph(obj, graph)
        refresh(obj, graph, context)
        graph = get_graph(obj)
        left = len(sym.unpaired_arcs(graph, st.symmetry_axis,
                                     st.symmetry_tolerance))
        self.report({"INFO"},
                    f"Dropped {len(doomed)} unpaired arc(s), mirrored the "
                    f"kept side — {left} still unpaired")
        return {"FINISHED"}


class NXLOOM_OT_fix_patch(bpy.types.Operator):
    """Apply the first repair that actually resolves the failing patches"""

    bl_idname = "nxloom.fix_patch"
    bl_label = "Fix It"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and obj.get("nx_loom_fixes"))

    def execute(self, context):
        from ..core.build import _solve_counts
        from ..core.graph import LayoutGraph

        obj = active_object(context)
        st = context.scene.nx_loom
        graph = get_graph(obj)
        target = set(obj.get("nx_loom_bad_patches", []) or [])
        stored = obj.get("nx_loom_fixes", {}) or {}
        fixes = [(stored[k]["label"], stored[k]["kind"], stored[k]["payload"])
                 for k in sorted(stored.keys(), key=int)]

        def apply_fix(g, kind, payload):
            if kind == "unlock":
                if payload in g.arcs:
                    g.arcs[int(payload)].n_lock = None
            elif kind == "densify":
                if int(payload) in g.patches:
                    g.set_density(int(payload),
                                  g.patch_density(int(payload)) * 1.6)

        for label, kind, payload in fixes:
            # every candidate is validated on a copy before touching the
            # real document — a "fix" that fails or breaks something new is
            # never applied
            trial = LayoutGraph.from_json(graph.to_json())
            apply_fix(trial, kind, int(payload))
            _c, qrep = _solve_counts(trial, st.target_edge,
                                     st.fill_background)
            still = set(qrep.get("unsatisfied_patches", []))
            if target & still:
                continue
            if len(still) > len(target):
                continue
            apply_fix(graph, kind, int(payload))
            set_graph(obj, graph)
            obj["nx_loom_pin_warn"] = ""
            refresh(obj, graph, context)
            bpy.ops.ed.undo_push(message=f"NX Loom: {label}")
            self.report({"INFO"}, f"Fixed: {label}")
            return {"FINISHED"}

        self.report({"WARNING"},
                    "No single change resolves this — the diagnosis text "
                    "says what the patch needs")
        return {"CANCELLED"}


class NXLOOM_OT_smooth_arcs(bpy.types.Operator):
    """Fair hand jitter out of the selected arc, or all freehand arcs"""

    bl_idname = "nxloom.smooth_arcs"
    bl_label = "Smooth Arcs"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj)

    def execute(self, context):
        obj = active_object(context)
        graph = get_graph(obj)
        surface = _surface_of(graph, context)
        project = surface.project if surface is not None else None
        aid = active_arc(obj)
        targets = [aid] if aid is not None and aid in graph.arcs else [
            a for a, arc in graph.arcs.items()
            if arc.rail == "surface" and arc.mirror_of is None]
        n = 0
        for a in targets:
            arc = graph.arcs[a]
            if len(arc.path) < 4:
                continue
            arc.path = fair_path(arc.path, iters=10, strength=0.5,
                                 project=project)
            if surface is not None:
                arc.pins = [surface.pin(pt) for pt in arc.path]
            n += 1
        set_graph(obj, graph)
        refresh(obj, graph, context)
        self.report({"INFO"}, f"{n} arc(s) smoothed")
        return {"FINISHED"}


class NXLOOM_OT_repeat_ring(bpy.types.Operator):
    """Drop the next ring at the same spacing as the last two — press R"""

    bl_idname = "nxloom.repeat_ring"
    bl_label = "Repeat Ring"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj
                    and len(obj.get("nx_loom_ring_hist", []) or []) >= 2)

    def execute(self, context):
        obj = active_object(context)
        st = context.scene.nx_loom
        graph = get_graph(obj)
        surface = _surface_of(graph, context)
        if surface is None:
            self.report({"ERROR"}, "Set a Reference mesh first")
            return {"CANCELLED"}

        hist = [np.array(h, dtype=float)
                for h in obj.get("nx_loom_ring_hist", [])]
        prev, last = hist[-2], hist[-1]
        delta = last[:3] - prev[:3]
        pt = last[:3] + delta
        normal = last[3:6]
        anchor = last[6:9] + delta

        made = ring_from_plane(graph, surface, pt, normal, pt, anchor,
                               arc_type=st.arc_type)
        if made is None:
            self.report({"WARNING"},
                        "No closed loop there — the limb probably ended")
            return {"CANCELLED"}
        node_ids, _arcs = made
        bridged = None
        if st.bridge_rings:
            last_ring = list(obj.get("nx_loom_last_ring", []) or [])
            if last_ring and all(n in graph.nodes for n in last_ring):
                bridged = bridge_rings(graph, surface, last_ring, node_ids,
                                       arc_type=st.arc_type)
        obj["nx_loom_last_ring"] = [int(n) for n in node_ids]
        obj["nx_loom_ring_hist"] = [list(map(float, last)),
                                    list(map(float, list(pt) + list(normal)
                                             + list(anchor)))]
        refresh(obj, graph, context, rebuild=st.rebuild_on_draw)
        bpy.ops.ed.undo_push(message="NX Loom: repeat ring")
        self.report({"INFO"}, "Ring repeated"
                    + (" and bridged" if bridged else ""))
        return {"FINISHED"}


class NXLOOM_OT_loop_cut(bpy.types.Operator):
    """Insert a loop parallel to the arc under the cursor, through the whole
    quad strip — press C over an arc, click to place, Esc to cancel"""

    bl_idname = "nxloom.loop_cut"
    bl_label = "Loop Cut"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return _context_ok(context)

    def invoke(self, context, event):
        obj = active_object(context)
        self.graph = get_graph(obj)
        self.surface = _surface_of(self.graph, context) if self.graph else None
        if self.surface is None:
            self.report({"ERROR"}, "Set a Reference mesh first")
            return {"CANCELLED"}
        if not self.graph.patches:
            self.report({"WARNING"}, "Rebuild first — no patches to cut through")
            return {"CANCELLED"}
        span = float(np.linalg.norm(self.surface.verts.max(axis=0)
                                    - self.surface.verts.min(axis=0)))
        self.min_step = max(span * 0.004, 1e-6)
        self.plan = None
        context.window.cursor_modal_set("KNIFE")
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Hover an arc to preview the loop — click to cut, Esc cancels")
        self._preview(context, event)
        return {"RUNNING_MODAL"}

    def _preview(self, context, event):
        from ..core.loopcut import plan_loop

        point, hit = _arc_under(context, self.graph, event, self.surface)
        self.plan = None
        if hit is None:
            overlay.set_preview(None)
            return
        aid, _seg, _t, on_arc, _d = hit
        res = plan_loop(self.graph, aid, on_arc)
        if res is None:
            overlay.set_preview(None)
            return
        poly, closed = res
        poly = np.asarray(self.surface.project(poly), dtype=float)
        self.plan = (poly, closed)
        overlay.set_preview(path=poly, snap=on_arc)

    def _cleanup(self, context):
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)
        overlay.clear_preview()

    def modal(self, context, event):
        if event.type == "MOUSEMOVE":
            self._preview(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            self._cleanup(context)
            if self.plan is None:
                self.report({"WARNING"}, "No quad strip under the cursor")
                return {"CANCELLED"}
            poly, closed = self.plan
            st = context.scene.nx_loom
            obj = active_object(context)
            radius = _snap_radius(context, poly[0])
            if closed:
                # a closed loop has no free endpoints: commit as two halves so
                # each half has distinct anchors, like a cornerless ring
                mid = len(poly) // 2
                first = commit_path(self.graph, self.surface, poly[:mid + 1],
                                    radius, self.min_step,
                                    arc_type=st.arc_type)
                if first is None:
                    self.report({"WARNING"}, "Loop could not be committed")
                    return {"CANCELLED"}
                back = np.vstack([poly[mid:], poly[:1]])
                commit_path(self.graph, self.surface, back, radius,
                            self.min_step, arc_type=st.arc_type,
                            start_node=first[2])
            else:
                if commit_path(self.graph, self.surface, poly, radius,
                               self.min_step, arc_type=st.arc_type) is None:
                    self.report({"WARNING"}, "Loop could not be committed")
                    return {"CANCELLED"}
            refresh(obj, self.graph, context, rebuild=st.rebuild_on_draw)
            bpy.ops.ed.undo_push(message="NX Loom: loop cut")
            self.report({"INFO"}, "Loop inserted"
                        + (" (closed)" if closed else ""))
            return {"FINISHED"}
        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            self._cleanup(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}


class NXLOOM_OT_set_arc_type_key(bpy.types.Operator):
    """Set the arc type for new strokes from a number key"""

    bl_idname = "nxloom.set_arc_type_key"
    bl_label = "Arc Type Hotkey"
    bl_options = {"INTERNAL"}

    kind: bpy.props.StringProperty(default="flow")

    @classmethod
    def poll(cls, context):
        # without a layout the number keys must fall through to Blender's own
        # bindings (collection visibility) instead of being eaten
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj)

    def execute(self, context):
        context.scene.nx_loom.arc_type = self.kind
        self.report({"INFO"}, f"Drawing {self.kind} arcs")
        return {"FINISHED"}


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
        graph = peek_graph(obj)          # read-only: hover never edits
        surface = _surface_of(graph, context) if graph is not None else None
        if surface is None:
            overlay.clear_hover()
            return {"PASS_THROUGH"}

        origin, direction = _mouse_ray(context, event)
        point = ray_surface(surface, origin, direction)
        if point is None:
            overlay.clear_hover()
            return {"PASS_THROUGH"}

        snapped, on_seam = plane_snap(point, _seam_plane(context, point), surface)
        overlay.set_seam(snapped if on_seam else None)
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


class NXLOOM_OT_adjust_loops(bpy.types.Operator):
    """Pin the number of loops across the arc under the cursor

    The global solve keeps every patch closed, so pinning one arc ripples
    through the rest of the model. That is the whole mechanism, and it is
    invisible until you can grab a number and watch it propagate.
    """

    bl_idname = "nxloom.adjust_loops"
    bl_label = "Adjust Loops"
    bl_options = {"REGISTER", "UNDO"}

    delta: bpy.props.IntProperty(name="Change", default=1)

    @classmethod
    def poll(cls, context):
        return _context_ok(context)

    def invoke(self, context, event):
        obj = active_object(context)
        graph = get_graph(obj)
        surface = _surface_of(graph, context) if graph is not None else None
        if surface is None:
            self.report({"ERROR"}, "Set a Reference mesh first")
            return {"CANCELLED"}
        _, hit = _arc_under(context, graph, event, surface)
        if hit is None:
            self.report({"WARNING"}, "No arc under the cursor")
            return {"CANCELLED"}

        arc = graph.arcs[hit[0]]
        current = arc.n_lock if arc.n_lock else (arc.n or 1)
        want = max(1, int(current) + int(self.delta))
        arc.n_lock = want
        set_graph(obj, graph)
        set_active_arc(obj, hit[0])
        context.scene.nx_loom["active_loops"] = want
        # The mesh catches up once the wheel stops; rebuilding on every notch
        # is what let the events queue and the count run away.
        queue_rebuild(obj)
        self.report({"INFO"}, f"Arc {hit[0]} pinned to {want} loops")
        return {"FINISHED"}


class NXLOOM_OT_clear_loop_locks(bpy.types.Operator):
    """Unpin every arc and let the size settings decide again"""

    bl_idname = "nxloom.clear_loop_locks"
    bl_label = "Clear Loop Pins"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        graph = get_graph(obj) if obj is not None and GRAPH_KEY in obj else None
        return bool(graph and any(a.n_lock for a in graph.arcs.values()))

    def execute(self, context):
        obj = active_object(context)
        graph = get_graph(obj)
        n = 0
        for arc in graph.arcs.values():
            if arc.n_lock:
                arc.n_lock = None
                n += 1
        set_graph(obj, graph)
        refresh(obj, graph, context)
        self.report({"INFO"}, f"{n} arc(s) unpinned")
        return {"FINISHED"}


def _patch_under(context, obj, graph, event):
    """Which patch the cursor is over, via the face attribute build stamps."""
    origin, direction = _mouse_ray(context, event)
    mw_inv = obj.matrix_world.inverted()
    lo = mw_inv @ Vector(tuple(origin))
    ld = (mw_inv.to_3x3() @ Vector(tuple(direction))).normalized()
    hit, _, _, face = obj.ray_cast(lo, ld)
    if hit and face >= 0:
        attr = obj.data.attributes.get("nx_loom_patch")
        if attr is not None and face < len(attr.data):
            pid = int(attr.data[face].value)
            if pid in graph.patches:
                return pid
    return None


class NXLOOM_OT_adjust_patch_density(bpy.types.Operator):
    """Ask for more or less resolution inside the patch under the cursor"""

    bl_idname = "nxloom.adjust_patch_density"
    bl_label = "Patch Density"
    bl_options = {"REGISTER", "UNDO"}

    factor: bpy.props.FloatProperty(name="Factor", default=1.25)

    @classmethod
    def poll(cls, context):
        return _context_ok(context)

    def invoke(self, context, event):
        obj = active_object(context)
        graph = get_graph(obj)
        if graph is None:
            return {"CANCELLED"}
        pid = _patch_under(context, obj, graph, event)
        if pid is None:
            self.report({"WARNING"}, "No generated face under the cursor")
            return {"CANCELLED"}
        now = graph.patch_density(pid)
        want = float(np.clip(now * self.factor, 0.2, 5.0))
        graph.set_density(pid, want)
        set_graph(obj, graph)
        refresh(obj, graph, context)
        self.report({"INFO"}, f"Patch {pid} density {want:.2f}x")
        return {"FINISHED"}


class NXLOOM_OT_clear_patch_density(bpy.types.Operator):
    """Return every patch to the global size settings"""

    bl_idname = "nxloom.clear_patch_density"
    bl_label = "Clear Patch Density"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        graph = get_graph(obj) if obj is not None and GRAPH_KEY in obj else None
        return bool(graph and graph.settings.get("density"))

    def execute(self, context):
        obj = active_object(context)
        graph = get_graph(obj)
        n = len(graph.settings.get("density", {}))
        graph.settings["density"] = {}
        set_graph(obj, graph)
        refresh(obj, graph, context)
        self.report({"INFO"}, f"{n} patch density override(s) cleared")
        return {"FINISHED"}


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

        pid = _patch_under(context, obj, graph, event)

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
    NXLOOM_OT_ring_cut,
    NXLOOM_OT_halo,
    NXLOOM_OT_loop_cut,
    NXLOOM_OT_repeat_ring,
    NXLOOM_OT_set_arc_type_key,
    NXLOOM_OT_symmetrize_side,
    NXLOOM_OT_fix_patch,
    NXLOOM_OT_smooth_arcs,
    NXLOOM_OT_hover,
    NXLOOM_OT_adjust_loops,
    NXLOOM_OT_select_arc,
    NXLOOM_OT_unpin_arc,
    NXLOOM_OT_clear_loop_locks,
    NXLOOM_OT_adjust_patch_density,
    NXLOOM_OT_clear_patch_density,
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
