"""Radial symmetry: author one wedge, radialize the rest.

N-fold rotational copies of the authored arcs around a chosen axis, emitted
as suggestion GHOSTS — the accept lane's snapping and crossing machinery
welds the wedge borders, so there is no separate weld pass to get wrong.
Copies that would land on geometry that already exists are skipped, which
makes the operator idempotent: radialize twice, get nothing new.
"""

from __future__ import annotations

import bpy
import numpy as np

from ..core.graph import GRAPH_KEY
from ..ui import overlay
from .draw import _surface_of
from .layout import active_object, get_graph, set_graph

AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}


def _rotation(axis, angle):
    c, s = np.cos(angle), np.sin(angle)
    if axis == 0:
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == 1:
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def radial_ghosts(paths, count, axis, span, project=None):
    """Rotated copies of ``paths`` that are not already covered by them."""
    soup = np.concatenate([np.asarray(p, dtype=float) for p in paths]) \
        if paths else np.zeros((0, 3))
    tol = max(span * 0.02, 1e-6)
    ghosts = []
    for k in range(1, count):
        R = _rotation(axis, 2.0 * np.pi * k / count)
        for path in paths:
            rp = np.asarray(path, dtype=float) @ R.T
            if project is not None:
                rp = np.asarray(project(rp), dtype=float)
            probes = rp[:: max(len(rp) // 6, 1)]
            d = np.array([float(np.linalg.norm(soup - q, axis=1).min())
                          for q in probes])
            if float(np.median(d)) < tol:
                continue                  # that copy is already authored
            ghosts.append(rp)
    return ghosts


class NXLOOM_OT_radialize(bpy.types.Operator):
    """Propose N-fold rotational copies of the authored arcs as ghosts —
    draw one wedge, accept the rest. Copies that already exist are skipped"""

    bl_idname = "nxloom.radialize"
    bl_label = "Radialize"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj)

    def execute(self, context):
        st = context.scene.nx_loom
        obj = active_object(context)
        graph = get_graph(obj)
        paths = [np.asarray(a.path, dtype=float)
                 for a in graph.arcs.values()
                 if a.mirror_of is None and len(a.path) >= 2]
        if not paths:
            self.report({"ERROR"}, "Nothing authored to radialize yet")
            return {"CANCELLED"}
        surface = _surface_of(graph, context)
        span = 1.0
        if surface is not None and len(surface.verts):
            span = float(np.linalg.norm(surface.verts.max(axis=0)
                                        - surface.verts.min(axis=0)))
        ghosts = radial_ghosts(
            paths, int(st.radial_count), AXIS_INDEX[st.radial_axis], span,
            project=surface.project if surface is not None else None)
        if not ghosts:
            self.report({"INFO"},
                        "Nothing new — every rotational copy already exists")
            return {"CANCELLED"}
        stored = list(graph.settings.get("suggestions") or [])
        types = list(graph.settings.get("suggestion_types") or [])
        types += [""] * (len(stored) - len(types))
        graph.settings["suggestions"] = stored + [
            [float(x) for p in g for x in p] for g in ghosts]
        graph.settings["suggestion_types"] = types + [""] * len(ghosts)
        set_graph(obj, graph)
        overlay.mark_dirty()
        self.report({"INFO"},
                    f"{len(ghosts)} rotational cop(ies) proposed as ghosts "
                    f"— accept or discard them")
        return {"FINISHED"}


def register():
    bpy.utils.register_class(NXLOOM_OT_radialize)


def unregister():
    bpy.utils.unregister_class(NXLOOM_OT_radialize)
