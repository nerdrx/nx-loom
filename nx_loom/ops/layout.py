"""Operators: author a layout, rebuild it, apply it."""

import bmesh
import bpy
import numpy as np
from mathutils import Vector

from ..core import delta as delta_mod
from ..core import symmetry as sym
from ..core.build import build, mesh_stats, solve_edge_for_count
from ..core.graph import GRAPH_KEY, LayoutGraph, from_edge_chains, trace_chains
from ..core.surface import Surface, cached_surface

DELTA_KEY = delta_mod.DELTA_KEY


def active_object(context):
    """The active object, safely.

    ``context.active_object`` simply does not exist in a restricted context —
    a timer, a handler, a driver — and touching it there raises rather than
    returning None. A poll that raises spams the console on every redraw, so
    every poll in this addon goes through here.
    """
    obj = getattr(context, "active_object", None)
    if obj is not None:
        return obj
    view_layer = getattr(context, "view_layer", None)
    if view_layer is None:
        return None
    return getattr(view_layer.objects, "active", None)


def get_graph(obj):
    text = obj.get(GRAPH_KEY) if obj else None
    return LayoutGraph.from_json(text) if text else None


def set_graph(obj, graph):
    obj[GRAPH_KEY] = graph.to_json()


def _surface_for(graph, context):
    name = graph.reference
    ref = bpy.data.objects.get(name) if name else None
    if ref is None:
        ref = context.scene.nx_loom.reference
    return cached_surface(ref, context.evaluated_depsgraph_get()) if ref else None


def rebuild_object(obj, context, report_fn=None):
    """Regenerate obj's mesh from its layout graph. Returns the report."""
    graph = get_graph(obj)
    if graph is None:
        return None
    st = context.scene.nx_loom
    surface = _surface_for(graph, context)
    if surface is not None:
        graph.refresh_positions(surface)
    sym.sync(graph, st.symmetry_axis, st.symmetry_tolerance, surface)
    graph.discover_patches(
        normal_at=surface.normal_at if surface else None,
        corner_angle=st.corner_angle,
    )
    project = surface.project if (surface and st.reproject) else None

    edge = st.target_edge
    solved = None
    if st.size_mode == "COUNT":
        edge, solved = solve_edge_for_count(graph, st.target_count,
                                            st.fill_background)

    verts, quads, prov, report = build(
        graph, target_edge=edge, project=project,
        relax_iters=st.relax_iters, fill_background=st.fill_background,
    )

    if st.symmetry_axis != "NONE" and len(verts):
        verts, symrep = sym.symmetrize_verts(verts, st.symmetry_axis,
                                             st.symmetry_tolerance)
        report["symmetry"] = symrep

    if solved is not None:
        report["requested_count"] = st.target_count
        report["solved_edge"] = edge

    deltas = delta_mod.load(obj)
    if deltas["offsets"] and len(verts):
        normal_fn = surface.normal_at if surface else (lambda p: (0.0, 0.0, 1.0))
        verts, dstats = delta_mod.apply_deltas(verts, prov, normal_fn, deltas)
        report["delta"] = dstats
    report.update(mesh_stats(verts, quads))

    mw_inv = obj.matrix_world.inverted()
    local = [tuple(mw_inv @ Vector(tuple(v))) for v in verts]

    mesh = obj.data
    mesh.clear_geometry()
    mesh.from_pydata(local, [], quads)
    mesh.update()

    # Stamp which patch each face came from, so clicking a face can name a
    # patch without re-deriving anything.
    qp = report.get("quad_patch") or []
    if qp and len(qp) == len(mesh.polygons):
        attr = mesh.attributes.get("nx_loom_patch")
        if attr is None or attr.domain != "FACE" or attr.data_type != "INT":
            if attr is not None:
                mesh.attributes.remove(attr)
            attr = mesh.attributes.new("nx_loom_patch", "INT", "FACE")
        attr.data.foreach_set("value", qp)

    set_graph(obj, graph)
    if report_fn:
        report_fn(report)
    return report


class NXLOOM_OT_layout_from_selection(bpy.types.Operator):
    """Turn the selected edges into a layout and generate a mesh from it"""

    bl_idname = "nxloom.layout_from_selection"
    bl_label = "Layout from Selected Edges"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj and obj.type == "MESH" and obj.mode == "EDIT")

    def execute(self, context):
        src = active_object(context)
        bm = bmesh.from_edit_mesh(src.data)
        sel = [e for e in bm.edges if e.select]
        if not sel:
            self.report({"ERROR"}, "No edges selected")
            return {"CANCELLED"}

        mw = src.matrix_world
        used = sorted({v.index for e in sel for v in e.verts})
        remap = {vi: i for i, vi in enumerate(used)}
        bm.verts.ensure_lookup_table()
        pts = [tuple(mw @ bm.verts[vi].co) for vi in used]
        edges = [(remap[e.verts[0].index], remap[e.verts[1].index]) for e in sel]

        st = context.scene.nx_loom
        chains = trace_chains(edges, pts, corner_angle=st.corner_angle)
        graph = from_edge_chains(pts, chains, reference=src.name)
        graph.settings["target_edge"] = st.target_edge

        bpy.ops.object.mode_set(mode="OBJECT")
        surface = cached_surface(src, context.evaluated_depsgraph_get())
        for node in graph.nodes.values():
            node.pin = surface.pin(node.co)
        for arc in graph.arcs.values():
            arc.pins = [surface.pin(p) for p in arc.path]

        drep = graph.discover_patches(
            normal_at=surface.normal_at, corner_angle=st.corner_angle
        )
        if not graph.patches:
            self.report({"ERROR"},
                        f"No patches found — the selection must enclose areas "
                        f"({drep['cycles']} cycles, {drep['rejected']})")
            return {"CANCELLED"}

        mesh = bpy.data.meshes.new(f"{src.name}_loom")
        obj = bpy.data.objects.new(f"{src.name}_loom", mesh)
        obj.show_in_front = True
        context.collection.objects.link(obj)
        set_graph(obj, graph)
        if st.reference is None:
            st.reference = src

        rep = rebuild_object(obj, context)
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        self.report(
            {"INFO"},
            f"{len(graph.patches)} patches, {rep['quads']} quads, "
            f"{len(graph.arcs)} arcs" +
            (f" — {len(rep['failed_patches'])} unfilled" if rep["failed_patches"] else "")
        )
        return {"FINISHED"}


class NXLOOM_OT_new_layout(bpy.types.Operator):
    """Start an empty layout pinned to the active mesh, ready to draw on"""

    bl_idname = "nxloom.new_layout"
    bl_label = "New Layout"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj and obj.type == "MESH" and GRAPH_KEY not in obj)

    def execute(self, context):
        src = active_object(context)
        graph = LayoutGraph(reference=src.name)
        graph.settings["target_edge"] = context.scene.nx_loom.target_edge

        mesh = bpy.data.meshes.new(f"{src.name}_loom")
        obj = bpy.data.objects.new(f"{src.name}_loom", mesh)
        obj.matrix_world = src.matrix_world.copy()
        # The generated mesh sits exactly on the reference surface, so without
        # this the two z-fight from the first face onwards.
        obj.show_in_front = True
        context.collection.objects.link(obj)
        set_graph(obj, graph)
        obj["nx_loom_bad_patches"] = []

        if context.scene.nx_loom.reference is None:
            context.scene.nx_loom.reference = src
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        self.report({"INFO"}, f"Empty layout on {src.name} — pick the Draw Arc tool")
        return {"FINISHED"}


def clean_build(obj, context):
    """Rebuild without the delta layer. Returns (verts, provenance, surface).

    Capture works by differencing the edited mesh against this, so the stored
    offsets are always relative to the pristine generated surface rather than
    accumulating on top of themselves.
    """
    graph = get_graph(obj)
    if graph is None:
        return None, None, None
    st = context.scene.nx_loom
    surface = _surface_for(graph, context)
    if surface is not None:
        graph.refresh_positions(surface)
    sym.sync(graph, st.symmetry_axis, st.symmetry_tolerance, surface)
    graph.discover_patches(
        normal_at=surface.normal_at if surface else None,
        corner_angle=st.corner_angle,
    )
    project = surface.project if (surface and st.reproject) else None
    edge = st.target_edge
    if st.size_mode == "COUNT":
        edge, _ = solve_edge_for_count(graph, st.target_count, st.fill_background)
    verts, _, prov, _ = build(graph, target_edge=edge, project=project,
                              relax_iters=st.relax_iters,
                              fill_background=st.fill_background)
    # Must match rebuild_object exactly, symmetrisation included. Capture
    # differences the edited mesh against this, so anything rebuild does and
    # this does not gets recorded as if the artist had done it by hand.
    if st.symmetry_axis != "NONE" and len(verts):
        verts, _ = sym.symmetrize_verts(verts, st.symmetry_axis,
                                        st.symmetry_tolerance)
    return verts, prov, surface


class NXLOOM_OT_capture_edits(bpy.types.Operator):
    """Fold your hand edits into the layout so they survive a rebuild"""

    bl_idname = "nxloom.capture_edits"
    bl_label = "Capture Edits"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj)

    def execute(self, context):
        obj = active_object(context)
        st = context.scene.nx_loom
        if obj.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        clean, prov, surface = clean_build(obj, context)
        if clean is None:
            self.report({"ERROR"}, "No layout on this object")
            return {"CANCELLED"}

        mw = obj.matrix_world
        edited = np.array([tuple(mw @ v.co) for v in obj.data.vertices], dtype=float)
        if len(edited) != len(clean):
            self.report(
                {"ERROR"},
                f"Vertex count changed ({len(clean)} generated, {len(edited)} now). "
                "Move vertices to record an edit — adding or deleting them is a "
                "layout change, so draw it instead."
            )
            return {"CANCELLED"}

        normal_fn = surface.normal_at if surface else (lambda p: (0.0, 0.0, 1.0))
        mirror = st.symmetry_axis if (st.mirror_edits
                                      and st.symmetry_axis != "NONE") else None
        table = delta_mod.capture(clean, edited, prov, normal_fn,
                                  mirror_axis=mirror,
                                  seam_tol=st.symmetry_tolerance)
        delta_mod.store(obj, table)
        self.report({"INFO"}, f"{delta_mod.count(table)} edited vert(s) captured")
        return {"FINISHED"}


class NXLOOM_OT_clear_edits(bpy.types.Operator):
    """Discard captured hand edits and return to the generated surface"""

    bl_idname = "nxloom.clear_edits"
    bl_label = "Clear Edits"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and DELTA_KEY in obj)

    def execute(self, context):
        obj = active_object(context)
        delta_mod.store(obj, None)
        rebuild_object(obj, context)
        self.report({"INFO"}, "Hand edits discarded")
        return {"FINISHED"}


class NXLOOM_OT_rebuild(bpy.types.Operator):
    """Regenerate the mesh from its layout graph"""

    bl_idname = "nxloom.rebuild"
    bl_label = "Rebuild"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj)

    def execute(self, context):
        rep = rebuild_object(active_object(context), context)
        if rep is None:
            self.report({"ERROR"}, "No layout on this object")
            return {"CANCELLED"}
        msg = f"{rep['quads']} quads, {rep['verts']} verts"
        if rep["failed_patches"] or rep["unsatisfied_patches"]:
            msg += (f" — {len(rep['unsatisfied_patches'])} unquantized, "
                    f"{len(rep['failed_patches'])} unfilled")
            self.report({"WARNING"}, msg)
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class NXLOOM_OT_apply(bpy.types.Operator):
    """Drop the layout and leave an ordinary mesh, carrying the reference's data"""

    bl_idname = "nxloom.apply"
    bl_label = "Apply"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj)

    def execute(self, context):
        obj = active_object(context)
        st = context.scene.nx_loom
        graph = get_graph(obj)
        note = ""

        if st.transfer_data and graph is not None:
            ref = bpy.data.objects.get(graph.reference) if graph.reference else None
            if ref is None:
                ref = st.reference
            if ref is not None and ref is not obj and ref.type == "MESH":
                from ..core.vendor import qf_transfer
                snap = None
                try:
                    snap = qf_transfer.capture(ref)
                    rep = qf_transfer.apply(snap, obj)
                    note = (f" — UVs {rep['uv_layers']}, groups {rep['weights']}, "
                            f"keys {rep['shape_keys']}, materials {rep['materials']}")
                    for w in rep.get("warnings", ())[:2]:
                        self.report({"WARNING"}, f"transfer: {w}")
                except Exception as e:
                    self.report({"WARNING"}, f"Data transfer failed: {e}")
                finally:
                    if snap is not None:
                        try:
                            snap.free()
                        except Exception:
                            pass
            else:
                self.report({"WARNING"}, "No reference mesh — nothing to transfer")

        del obj[GRAPH_KEY]
        if DELTA_KEY in obj:
            del obj[DELTA_KEY]
        for key in ("nx_loom_bad_patches", "nx_loom_background"):
            if key in obj:
                del obj[key]
        attr = obj.data.attributes.get("nx_loom_patch")
        if attr is not None:
            obj.data.attributes.remove(attr)
        obj.show_in_front = False
        self.report({"INFO"}, f"Layout applied — this is a plain mesh now{note}")
        return {"FINISHED"}


class NXLOOM_OT_generate_uvs(bpy.types.Operator):
    """Unwrap straight from the layout — the patch grid IS the UV grid"""

    bl_idname = "nxloom.generate_uvs"
    bl_label = "UVs from Layout"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj
                    and len(obj.data.polygons) > 0)

    def execute(self, context):
        from ..core import uv as uv_mod

        obj = active_object(context)
        report = rebuild_object(obj, context)
        if not report:
            return {"CANCELLED"}
        graph = get_graph(obj)
        mw = obj.matrix_world
        world = [tuple(mw @ v.co) for v in obj.data.vertices]
        faces = [tuple(p.vertices) for p in obj.data.polygons]
        packed = uv_mod.layout_uvs(graph, report.get("counts", {}), report,
                                   verts=world, quads=faces)
        coords, uv_info = packed if packed else (None, {})
        if coords is None or len(coords) != len(obj.data.polygons):
            self.report({"ERROR"}, "Could not lay out UVs — rebuild first")
            return {"CANCELLED"}

        me = obj.data
        layer = me.uv_layers.get("NXLoom") or me.uv_layers.new(name="NXLoom")
        me.uv_layers.active = layer
        flat = []
        for poly, quad in zip(me.polygons, coords):
            for k in range(len(poly.loop_indices)):
                flat.extend(quad[k] if k < len(quad) else quad[-1])
        layer.data.foreach_set("uv", flat)
        me.update()

        obj["nx_loom_uv_islands"] = uv_info.get("islands", 0)
        self.report({"INFO"},
                    f"{uv_info.get('islands', 0)} island(s) from "
                    f"{len(graph.patches)} patches onto {len(me.polygons)} faces")
        return {"FINISHED"}


class NXLOOM_OT_activate_draw_tool(bpy.types.Operator):
    """Switch to the Loom Draw tool in the toolbar"""

    bl_idname = "nxloom.activate_draw_tool"
    bl_label = "Draw Arcs"

    def execute(self, context):
        try:
            bpy.ops.wm.tool_set_by_id(name="nxloom.draw_tool")
        except Exception as e:
            self.report({"WARNING"}, f"Pick Loom Draw in the toolbar ({e})")
            return {"CANCELLED"}
        return {"FINISHED"}


class NXLOOM_OT_toggle_reference(bpy.types.Operator):
    """Show or hide the reference mesh the layout is drawn on"""

    bl_idname = "nxloom.toggle_reference"
    bl_label = "Toggle Reference"

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and GRAPH_KEY in obj)

    def execute(self, context):
        graph = get_graph(active_object(context))
        ref = bpy.data.objects.get(graph.reference) if graph else None
        if ref is None:
            ref = context.scene.nx_loom.reference
        if ref is None:
            self.report({"WARNING"}, "No reference mesh")
            return {"CANCELLED"}
        ref.hide_set(not ref.hide_get())
        return {"FINISHED"}


class NXLOOM_OT_frame_problem(bpy.types.Operator):
    """Move the view to the first patch the solver could not resolve"""

    bl_idname = "nxloom.frame_problem"
    bl_label = "Show Problem Patch"

    @classmethod
    def poll(cls, context):
        obj = active_object(context)
        return bool(obj is not None and obj.get("nx_loom_bad_patches"))

    def execute(self, context):
        obj = active_object(context)
        graph = get_graph(obj)
        bad = list(obj.get("nx_loom_bad_patches", []) or [])
        if graph is None or not bad:
            return {"CANCELLED"}
        pid = next((p for p in bad if p in graph.patches), None)
        if pid is None:
            self.report({"WARNING"}, "That patch no longer exists — rebuild")
            return {"CANCELLED"}
        pts = graph.patch_boundary(pid)
        if not len(pts):
            return {"CANCELLED"}
        centre = Vector(tuple(pts.mean(axis=0)))
        context.scene.cursor.location = centre
        rv3d = getattr(context.space_data, "region_3d", None)
        if rv3d is not None:
            rv3d.view_location = centre
        self.report({"INFO"}, f"Patch {pid}: {len(graph.patches[pid].sides)} sides")
        return {"FINISHED"}


_CLASSES = (
    NXLOOM_OT_new_layout,
    NXLOOM_OT_generate_uvs,
    NXLOOM_OT_activate_draw_tool,
    NXLOOM_OT_toggle_reference,
    NXLOOM_OT_frame_problem,
    NXLOOM_OT_capture_edits,
    NXLOOM_OT_clear_edits,
    NXLOOM_OT_layout_from_selection,
    NXLOOM_OT_rebuild,
    NXLOOM_OT_apply,
)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)


