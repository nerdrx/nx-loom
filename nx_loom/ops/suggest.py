"""Suggestion-lane operators. SPEC §7 rules: proposals only, never applied on
their own, and a lane with nothing confident to offer offers nothing."""

from __future__ import annotations

import bpy
import numpy as np

from ..core.graph import GRAPH_KEY
from ..core.suggest import suggest_iter
from ..ui import overlay
from .draw import _surface_of, commit_path, refresh
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

        from . import jobs
        cost = max(float(obj.get("nx_loom_suggest_ms", 0.0) or 0.0),
                   500.0 if len(ref.data.polygons) > 20000 else 0.0)
        if jobs.should_run_async(context, cost):
            name = obj.name

            def _done(res, why, _name=name):
                if res is not None:
                    jobs.JOB["note"] = str(res[1])
            if jobs.start("Suggest Arcs", _suggest_job(obj, ref, context),
                          on_done=_done,
                          budget=context.scene.nx_loom.job_budget or None):
                self.report({"INFO"},
                            "Suggesting in the background — progress and "
                            "Cancel are in the sidebar")
                return {"FINISHED"}

        level, msg = jobs_drain(_suggest_job(obj, ref, context))
        self.report({level}, msg)
        return {"CANCELLED"} if level == "ERROR" else {"FINISHED"}


def jobs_drain(gen):
    from ..core.budget import drain
    return drain(gen)


def _suggest_job(obj, ref, context):
    """The whole suggest flow as a progress generator. Ghosts are written
    only after the final yield — cancelling proposes nothing."""
    import time as _time
    t_start = _time.monotonic()
    graph = get_graph(obj)
    yield (0.02, "preparing the reference")
    proxy = _proxy_tris(ref, context)
    if proxy is None:
        return ("ERROR", "Could not read the reference")
    verts, tris = proxy

    st = context.scene.nx_loom
    if st.symmetry_axis != "NONE":
        # solve on ONE half only: the field and the tracer are numerical
        # and their output is never mirror-exact, so a full-body solve
        # yields visibly asymmetric proposals with symmetry on. One-sided
        # proposals are what the rest of the pipeline expects anyway —
        # accepted arcs get mirrored by sync like anything authored.
        from ..core.symmetry import AXIS_INDEX
        ax = AXIS_INDEX[st.symmetry_axis]
        cent = verts[tris].mean(axis=1)[:, ax]
        keep = cent >= -st.symmetry_tolerance
        tris = tris[keep]
        used = np.unique(tris)
        remap = np.full(len(verts), -1, dtype=int)
        remap[used] = np.arange(len(used))
        verts = verts[used]
        tris = remap[tris]

    guides = [np.asarray(a.path, dtype=float)
              for a in graph.arcs.values() if len(a.path) >= 2]
    gen = suggest_iter(verts, tris, guides=guides)
    while True:
        try:
            item = next(gen)
        except StopIteration as stop:
            polylines, sing = stop.value
            break
        if item is not None:
            yield (0.05 + 0.9 * float(item[0]), item[1])

    if st.symmetry_axis != "NONE" and polylines:
        # clip proposals out of the seam band: a trace hugging the mirror
        # line proposes nothing the seam does not already own, and its
        # near-coincident mirror image only breeds unpaired-arc warnings
        from ..core.symmetry import AXIS_INDEX
        ax = AXIS_INDEX[st.symmetry_axis]
        span = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0)))
        band = max(st.symmetry_tolerance * 4.0, span * 0.02)
        clipped = []
        for poly in polylines:
            mask = poly[:, ax] >= band
            i = 0
            while i < len(poly):
                if not mask[i]:
                    i += 1
                    continue
                j = i
                while j + 1 < len(poly) and mask[j + 1]:
                    j += 1
                if j - i + 1 >= 6:
                    clipped.append(poly[i:j + 1])
                i = j + 1
        polylines = clipped
    obj["nx_loom_suggest_ms"] = (_time.monotonic() - t_start) * 1000.0
    if not polylines:
        # a lane with nothing confident to offer offers nothing
        graph.settings["suggestions"] = []
        set_graph(obj, graph)
        overlay.mark_dirty()
        return ("INFO",
                "No confident suggestions on this surface — its "
                "curvature does not pin down an edge flow here")

    graph.settings["suggestions"] = [
        [float(x) for p in poly for x in p] for poly in polylines]
    set_graph(obj, graph)
    overlay.mark_dirty()
    return ("INFO",
            f"{len(polylines)} arc(s) proposed from "
            f"{len(sing)} field pole(s) — accept or discard them")


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
            plane = None
            if st.symmetry_axis != "NONE" and st.seam_snap:
                from ..core.symmetry import AXIS_INDEX
                plane = (AXIS_INDEX[st.symmetry_axis], span * 0.01)
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
