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


def cylinder_rings(det, verts, target_edge, project=None):
    """Ghost rings for every detected cylinder: mathematically round,
    axis-perpendicular, emitted as quarter arcs so accepting gives each
    ring its corners (the ring-cut convention)."""
    ghosts = []
    for region in det:
        if region["kind"] != "cylinder":
            continue
        axis = np.asarray(region["axis"], dtype=float)
        centre = np.asarray(region["centre"], dtype=float)
        r = float(region["radius"])
        e1 = np.array([1.0, 0.0, 0.0])
        if abs(float(e1 @ axis)) > 0.9:
            e1 = np.array([0.0, 1.0, 0.0])
        e1 = e1 - axis * float(e1 @ axis)
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(axis, e1)
        h = region["heights"]
        lo, hi = float(h.min()), float(h.max())
        spacing = max(target_edge * 2.0, (hi - lo) / 24.0)
        n_rings = max(int(round((hi - lo) / spacing)) - 1, 1)
        for k in range(1, n_rings + 1):
            z = lo + (hi - lo) * k / (n_rings + 1)
            c = centre + axis * (z - float(centre @ axis))
            for q in range(4):
                ts = np.linspace(q * np.pi / 2, (q + 1) * np.pi / 2, 9)
                ring = (c[None, :] + np.outer(np.cos(ts), e1) * r
                        + np.outer(np.sin(ts), e2) * r)
                if project is not None:
                    ring = np.asarray(project(ring), dtype=float)
                ghosts.append(ring)
    return ghosts


class NXLOOM_OT_suggest_primitives(bpy.types.Operator):
    """Detect the planes and cylinders between the creases and propose
    exact rings on every cylinder — mathematically round, not traced"""

    bl_idname = "nxloom.suggest_primitives"
    bl_label = "From Primitives"
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

        from ..core.primitives import detect
        det = detect(verts, tris)
        va = np.asarray(verts, dtype=float)
        ta = np.asarray(tris, dtype=int)
        for region in det:
            if region["kind"] == "cylinder":
                # extent from the region's VERTICES — face centres of a
                # single tall quad row sit at a third of the height and
                # would starve the ring count
                region["heights"] = va[ta[region["faces"]]].reshape(-1, 3) \
                    @ np.asarray(region["axis"], dtype=float)
        surface = _surface_of(graph, context)
        ghosts = cylinder_rings(det, verts, st.target_edge,
                                project=surface.project
                                if surface is not None else None)
        span = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0)))
        ghosts = clip_one_sided(ghosts, st, span)

        # complete, don't redraw (same coverage skip as creases)
        soup = [np.asarray(a.path, dtype=float) for a in graph.arcs.values()
                if len(a.path)]
        if soup and ghosts:
            soup = np.concatenate(soup)
            tol = span * 0.02
            ghosts = [g for g in ghosts
                      if float(np.median(np.array(
                          [float(np.linalg.norm(soup - q, axis=1).min())
                           for q in g[:: max(len(g) // 4, 1)]]))) >= tol]

        n_cyl = sum(1 for r in det if r["kind"] == "cylinder")
        n_plane = sum(1 for r in det if r["kind"] == "plane")
        if not ghosts:
            self.report({"INFO"},
                        f"{n_cyl} cylinder(s), {n_plane} flat region(s) "
                        f"found — nothing new to propose"
                        + (". Mark flat patches with V to kill scan noise"
                           if n_plane else ""))
            return {"CANCELLED"}
        store_ghosts(graph, ghosts, arc_type="flow", append=True)
        set_graph(obj, graph)
        overlay.mark_dirty()
        self.report({"INFO"},
                    f"{len(ghosts) // 4} exact ring(s) on {n_cyl} "
                    f"cylinder(s) proposed; {n_plane} flat region(s) — "
                    f"V flattens a patch")
        return {"FINISHED"}


class NXLOOM_OT_toggle_flatten(bpy.types.Operator):
    """Mark the patch under the cursor as truly planar — its interior is
    projected onto the boundary's plane, so scan noise on a flat panel
    disappears — or unmark it"""

    bl_idname = "nxloom.toggle_flatten"
    bl_label = "Flatten Patch"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from .draw import _context_ok
        return _context_ok(context)

    def invoke(self, context, event):
        from .draw import _patch_under
        obj = active_object(context)
        graph = get_graph(obj)
        if graph is None or not graph.patches:
            return {"CANCELLED"}
        pid = _patch_under(context, obj, graph, event)
        if pid is None:
            self.report({"WARNING"}, "No patch under the cursor")
            return {"CANCELLED"}
        flag = not graph.is_flat(pid)
        graph.set_flat(pid, flag)
        set_graph(obj, graph)
        refresh(obj, graph, context)
        self.report({"INFO"},
                    f"Patch {pid} {'flattened onto its boundary plane'
                    if flag else 'follows the reference again'}")
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


_CLASSES = (NXLOOM_OT_suggest_creases, NXLOOM_OT_suggest_primitives,
            NXLOOM_OT_toggle_flatten, NXLOOM_OT_support_loops)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
