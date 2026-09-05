"""The hard-surface lane: creases become arcs, and edges stay sharp.

Suggest from Creases promotes the sculpt's own crease network (the same
feature curves the magnet snaps to) into ghost proposals typed ``crease`` —
one click puts an arc on every sharp edge, and constrained auto-complete
then fills the flats between them. Support Loops commits the defining
hard-surface pattern: parallel loops hugging a crease at a chosen offset,
via the loop-cut iso machinery, so subdivision cannot melt the edge.
"""

from __future__ import annotations

import bpy
import numpy as np

from ..core.graph import GRAPH_KEY
from ..core.loopcut import iso_across
from ..ui import overlay
from .draw import _surface_of, active_arc, commit_path, refresh
from .layout import active_object, get_graph, set_graph
from .suggest import _proxy_tris, clip_one_sided, store_ghosts


class NXLOOM_OT_suggest_creases(bpy.types.Operator):
    """Propose an arc on every decisive crease of the reference — ghosts
    typed as creases; accept them, then Suggest fills the flats between"""

    bl_idname = "nxloom.suggest_creases"
    bl_label = "From Creases"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj)

    def execute(self, context):
        st = context.scene.nx_loom
        obj = active_object(context)
        graph = get_graph(obj)
        ref = bpy.data.objects.get(graph.reference) if graph.reference \
            else None
        if ref is None:
            ref = st.reference
        if ref is None:
            self.report({"ERROR"}, "Set a Reference mesh first")
            return {"CANCELLED"}
        proxy = _proxy_tris(ref, context)
        if proxy is None:
            self.report({"ERROR"}, "Could not read the reference")
            return {"CANCELLED"}
        verts, tris = proxy

        from ..core.features import feature_curves
        curves = feature_curves(verts, tris)
        span = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0)))
        curves = clip_one_sided(curves, st, span)

        # complete, don't redraw: a crease that already carries an arc
        # offers nothing new
        soup = [np.asarray(a.path, dtype=float) for a in graph.arcs.values()
                if len(a.path)]
        if soup:
            soup = np.concatenate(soup)
            tol = span * 0.02
            kept = []
            for poly in curves:
                probes = poly[:: max(len(poly) // 6, 1)]
                d = np.array([float(np.linalg.norm(soup - q, axis=1).min())
                              for q in probes])
                if float(np.median(d)) >= tol:
                    kept.append(poly)
            curves = kept

        if not curves:
            self.report({"INFO"},
                        "No decisive creases to propose — this surface "
                        "has none the layout does not already carry")
            return {"CANCELLED"}
        store_ghosts(graph, curves, arc_type="crease", append=True)
        set_graph(obj, graph)
        overlay.mark_dirty()
        self.report({"INFO"},
                    f"{len(curves)} crease arc(s) proposed — accept them, "
                    f"then Suggest Arcs fills the flats between")
        return {"FINISHED"}


def crease_chain(graph, aid):
    """The selected arc plus its continuation through 2-valence joints of
    the same type — a crease is usually a chain, not one arc."""
    arc0 = graph.arcs.get(aid)
    if arc0 is None:
        return []
    chain = [aid]
    for start, node in ((aid, arc0.a), (aid, arc0.b)):
        cur, at = start, node
        while True:
            here = [a for a, arc in graph.arcs.items()
                    if a not in chain and at in (arc.a, arc.b)
                    and arc.type == arc0.type]
            total = [a for a, arc in graph.arcs.items()
                     if at in (arc.a, arc.b)]
            if len(here) != 1 or len(total) != 2:
                break
            cur = here[0]
            chain.append(cur)
            arc = graph.arcs[cur]
            at = arc.b if arc.a == at else arc.a
    return chain


class NXLOOM_OT_support_loops(bpy.types.Operator):
    """Add holding loops on both sides of the selected arc's chain — the
    hard-surface pattern that keeps an edge sharp under subdivision"""

    bl_idname = "nxloom.support_loops"
    bl_label = "Support Loops"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj
                    and active_arc(obj) is not None)

    def execute(self, context):
        st = context.scene.nx_loom
        obj = active_object(context)
        graph = get_graph(obj)
        aid = active_arc(obj)
        if aid is None or aid not in graph.arcs:
            self.report({"ERROR"}, "Select an arc first (Alt+Shift click)")
            return {"CANCELLED"}
        surface = _surface_of(graph, context)
        chain = crease_chain(graph, aid)
        offset = float(st.support_offset)

        # every quad patch touching the chain gets one iso-loop parallel to
        # the chain's side, at the offset fraction — keyed so a patch with
        # two chain arcs on one side contributes one loop, not two
        isos = {}
        for a in chain:
            for pid, patch in graph.patches.items():
                if len(patch.sides) != 4:
                    continue
                for i, side in enumerate(patch.sides):
                    if any(sa == a for sa, _rev in side):
                        isos.setdefault((pid, i), patch)
        if not isos:
            self.report({"ERROR"},
                        "No quad patches border this arc — support loops "
                        "need a filled quad on at least one side")
            return {"CANCELLED"}

        span = 1.0
        if surface is not None and len(surface.verts):
            span = float(np.linalg.norm(surface.verts.max(axis=0)
                                        - surface.verts.min(axis=0)))
        made = 0
        for (pid, i), patch in isos.items():
            iso = iso_across(graph, patch, (i + 1) % 4, offset, samples=14)
            if iso is None or len(iso) < 3:
                continue
            if surface is not None:
                iso = np.asarray(surface.project(iso), dtype=float)
            res = commit_path(graph, surface, iso, span * 0.008,
                              span * 0.004, arc_type="flow", smooth=0.0)
            if res is not None:
                made += 1
        if not made:
            self.report({"ERROR"},
                        "Could not lay a loop in any bordering patch")
            return {"CANCELLED"}
        set_graph(obj, graph)
        refresh(obj, graph, context)
        bpy.ops.ed.undo_push(message="NX Loom: support loops")
        self.report({"INFO"},
                    f"{made} support loop(s) hugging {len(chain)} arc(s) "
                    f"at {offset:.2f}")
        return {"FINISHED"}


_CLASSES = (NXLOOM_OT_suggest_creases, NXLOOM_OT_support_loops)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
