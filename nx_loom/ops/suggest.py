"""Suggestion-lane operators. SPEC §7 rules: proposals only, never applied on
their own, and a lane with nothing confident to offer offers nothing."""

from __future__ import annotations

import bpy
import numpy as np

from ..core.graph import GRAPH_KEY
from ..core.suggest import suggest
from ..ui import overlay
from .draw import _surface_of, commit_path, refresh, _seam_plane
from .layout import active_object, get_graph, set_graph

PROXY_FACES = 9000


def _proxy_tris(src, context, max_faces=PROXY_FACES):
    """The reference, decimated to field-solving size when it is a sculpt."""
    from ..core.surface import cached_surface

    surf = cached_surface(src, context.evaluated_depsgraph_get())
    if surf is None:
        return None
    if len(surf.tris) <= max_faces:
        return surf.verts, surf.tris

    tmp = src.copy()
    tmp.data = src.data.copy()
    context.collection.objects.link(tmp)
    mod = tmp.modifiers.new("nxloom_proxy", "DECIMATE")
    mod.ratio = max_faces / len(surf.tris)
    try:
        deps = context.evaluated_depsgraph_get()
        ev = tmp.evaluated_get(deps)
        me = ev.to_mesh()
        me.calc_loop_triangles()
        verts = np.array([tmp.matrix_world @ v.co for v in me.vertices],
                         dtype=float)
        tris = np.array([lt.vertices[:] for lt in me.loop_triangles],
                        dtype=int)
        ev.to_mesh_clear()
        return verts, tris
    finally:
        bpy.data.objects.remove(tmp, do_unlink=True)


class NXLOOM_OT_suggest(bpy.types.Operator):
    """Propose arcs from the sculpt's own curvature — ghosts to accept or
    discard, never applied on their own"""

    bl_idname = "nxloom.suggest_layout"
    bl_label = "Suggest Arcs"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj)

    def execute(self, context):
        obj = active_object(context)
        graph = get_graph(obj)
        ref = bpy.data.objects.get(graph.reference) if graph.reference else None
        if ref is None:
            ref = context.scene.nx_loom.reference
        if ref is None:
            self.report({"ERROR"}, "Set a Reference mesh first")
            return {"CANCELLED"}

        proxy = _proxy_tris(ref, context)
        if proxy is None:
            self.report({"ERROR"}, "Could not read the reference")
            return {"CANCELLED"}
        verts, tris = proxy
        polylines, sing = suggest(verts, tris)
        if not polylines:
            # a lane with nothing confident to offer offers nothing
            graph.settings["suggestions"] = []
            set_graph(obj, graph)
            self.report({"INFO"},
                        "No confident suggestions on this surface — its "
                        "curvature does not pin down an edge flow here")
            return {"FINISHED"}

        graph.settings["suggestions"] = [
            [float(x) for p in poly for x in p] for poly in polylines]
        set_graph(obj, graph)
        overlay.mark_dirty()
        self.report({"INFO"},
                    f"{len(polylines)} arc(s) proposed from "
                    f"{len(sing)} field pole(s) — accept or discard them")
        return {"FINISHED"}


class NXLOOM_OT_suggest_accept(bpy.types.Operator):
    """Commit every proposed arc as ordinary authored geometry"""

    bl_idname = "nxloom.suggest_accept"
    bl_label = "Accept Suggestions"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        if obj is None or GRAPH_KEY not in obj:
            return False
        graph = get_graph(obj)
        return bool(graph and graph.settings.get("suggestions"))

    def execute(self, context):
        obj = active_object(context)
        st = context.scene.nx_loom
        graph = get_graph(obj)
        surface = _surface_of(graph, context)
        stored = graph.settings.get("suggestions") or []
        graph.settings["suggestions"] = []

        span = 1.0
        if surface is not None and len(surface.verts):
            span = float(np.linalg.norm(surface.verts.max(axis=0)
                                        - surface.verts.min(axis=0)))
        snap = span * 0.012
        min_step = span * 0.004
        made = 0
        for flat in stored:
            poly = np.asarray(flat, dtype=float).reshape(-1, 3)
            if len(poly) < 3:
                continue
            if surface is not None:
                poly = np.asarray(surface.project(poly), dtype=float)
            plane = _seam_plane(context, poly[-1])
            res = commit_path(graph, surface, poly, snap, min_step,
                              arc_type=st.arc_type, smooth=0.25, plane=plane)
            if res is not None:
                made += 1
        set_graph(obj, graph)
        refresh(obj, graph, context)
        bpy.ops.ed.undo_push(message="NX Loom: accept suggestions")
        self.report({"INFO"},
                    f"{made} suggestion(s) are ordinary arcs now — edit them "
                    f"like anything you drew")
        return {"FINISHED"}


class NXLOOM_OT_suggest_clear(bpy.types.Operator):
    """Discard every proposed arc"""

    bl_idname = "nxloom.suggest_clear"
    bl_label = "Discard"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return NXLOOM_OT_suggest_accept.poll(context)

    def execute(self, context):
        obj = active_object(context)
        graph = get_graph(obj)
        n = len(graph.settings.get("suggestions") or [])
        graph.settings["suggestions"] = []
        set_graph(obj, graph)
        overlay.mark_dirty()
        self.report({"INFO"}, f"{n} suggestion(s) discarded")
        return {"FINISHED"}


_CLASSES = (NXLOOM_OT_suggest, NXLOOM_OT_suggest_accept,
            NXLOOM_OT_suggest_clear)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
