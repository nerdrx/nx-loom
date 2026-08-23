"""Viewport overlay for the layout graph.

The layout is the document, so it has to be the thing you can see. The
generated mesh is drawn by Blender as an ordinary mesh; this draws what
actually matters on top of it — arcs by type, nodes by role, and any patch the
solver refused, in red, before it becomes a hole you find later.
"""

from __future__ import annotations

import blf
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ..core.graph import GRAPH_KEY

# NX brand accent #7700FF, plus the palette allocated around it. One colour,
# one meaning: warm warning tones (orange, red, amber) belong to STATES only —
# crease arcs used to be nearly the same orange as the unpaired warning, which
# made the one alarm colour ambiguous.
ACCENT = (0.467, 0.0, 1.0, 1.0)
COL_ARC = {
    "flow": (0.66, 0.48, 1.0, 1.0),
    "crease": (0.93, 0.82, 1.0, 1.0),
    "boundary": (0.25, 0.85, 1.0, 0.95),
    "seam": (0.35, 1.0, 0.55, 0.95),
}
COL_FILL_BAD = (1.0, 0.18, 0.28, 0.16)
COL_FILL_BG = (0.5, 0.5, 0.55, 0.10)
COL_HOLE_EDGE = (0.3, 0.75, 0.8, 0.6)
COL_TICK = (1.0, 1.0, 1.0, 0.45)
COL_SEAM_CURVE = (0.35, 1.0, 1.0, 0.30)
COL_NODE = (0.72, 0.42, 1.0, 1.0)
COL_CORNER = (1.0, 1.0, 1.0, 1.0)
COL_BAD = (1.0, 0.18, 0.28, 1.0)
COL_PREVIEW = (1.0, 1.0, 1.0, 0.95)
COL_SNAP = (1.0, 0.85, 0.2, 1.0)
COL_HOVER = (1.0, 0.85, 0.2, 1.0)
COL_PINNED = (0.20, 1.0, 0.85, 1.0)
COL_ACTIVE = (1.0, 0.45, 0.85, 1.0)
COL_SEAM = (0.35, 1.0, 1.0, 1.0)
COL_UNPAIRED = (1.0, 0.55, 0.1, 0.95)

_handle = None
_handle_px = None
_preview = {"path": None, "snap": None, "anchor": None}
_hover = {"node": None, "arc": None, "seam": None}
_cache = {"key": None, "batches": None}


def set_preview(path=None, snap=None, anchor=None):
    _preview["path"] = path
    _preview["snap"] = snap
    _preview["anchor"] = anchor
    _tag_redraw()


def set_hover(node=None, arc=None):
    """What the cursor is over. Redraw only when it actually changes —
    this is fed from mouse-move, so an unconditional redraw would be a storm."""
    same = ((node is None) == (_hover["node"] is None)
            and (arc is None) == (_hover["arc"] is None))
    if same and node is not None and _hover["node"] is not None:
        same = bool(np.allclose(node, _hover["node"]))
    if same and arc is not None and _hover["arc"] is not None:
        same = (len(arc) == len(_hover["arc"])
                and bool(np.allclose(arc, _hover["arc"])))
    _hover["node"] = node
    _hover["arc"] = arc
    if not same:
        _tag_redraw()


def set_seam(point):
    """The cursor would land exactly on the symmetry plane — say so.

    Aiming for the middle by eye is a losing game, so the click snaps; this
    marker is what tells the artist the snap is armed before they commit.
    """
    changed = (point is None) != (_hover["seam"] is None)
    if not changed and point is not None and _hover["seam"] is not None:
        changed = not np.allclose(point, _hover["seam"])
    _hover["seam"] = None if point is None else np.asarray(point, dtype=float)
    if changed:
        _tag_redraw()


def clear_hover():
    set_hover(None, None)
    set_seam(None)


def clear_preview():
    set_preview(None, None, None)


def mark_dirty():
    _cache["key"] = None
    _tag_redraw()


def _tag_redraw():
    wm = bpy.context.window_manager
    if not wm:
        return
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _graph_of(obj):
    if obj is None or GRAPH_KEY not in obj:
        return None
    from ..ops.layout import peek_graph
    return peek_graph(obj)


def _segments(graph):
    """Arc polylines as line-list vertex pairs, grouped by arc type.

    A pinned arc is drawn in its own colour whatever its type: the loop count
    being held is what matters about it right then.
    """
    by_type = {}
    for arc in graph.arcs.values():
        path = np.asarray(arc.path, dtype=float)
        if len(path) < 2:
            continue
        kind = "pinned" if arc.n_lock else (arc.type if arc.type in COL_ARC
                                            else "flow")
        pairs = by_type.setdefault(kind, [])
        for i in range(len(path) - 1):
            pairs.append(tuple(path[i]))
            pairs.append(tuple(path[i + 1]))
    return by_type


def _bad_patch_loops(graph, bad_ids):
    verts = []
    for pid in bad_ids:
        patch = graph.patches.get(pid)
        if patch is None:
            continue
        for side in patch.sides:
            for aid, _ in side:
                arc = graph.arcs.get(aid)
                if arc is None:
                    continue
                path = np.asarray(arc.path, dtype=float)
                for i in range(len(path) - 1):
                    verts.append(tuple(path[i]))
                    verts.append(tuple(path[i + 1]))
    return verts


def _fan(pts):
    """Triangle fan over a boundary polygon, for a state tint. A fan from the
    centroid is not exact triangulation, but a translucent wash does not need
    exact — it needs cheap and roughly right."""
    pts = np.asarray(pts, dtype=float)
    if len(pts) < 3:
        return []
    c = tuple(pts.mean(axis=0))
    tris = []
    for i in range(len(pts)):
        tris.append(c)
        tris.append(tuple(pts[i]))
        tris.append(tuple(pts[(i + 1) % len(pts)]))
    return tris


def _build(graph, bad_ids, active=None, axis="NONE", extras=None):
    line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    point_shader = gpu.shader.from_builtin("UNIFORM_COLOR")

    batches = {"lines": [], "points": []}
    extras = extras or {}
    batches["tris"] = []

    # state washes: a failing region tints red, the background region dims —
    # visible at a glance instead of inferred from outlines
    if extras.get("show_fills", True):
        for pid in bad_ids:
            if pid in graph.patches:
                tris = _fan(graph.patch_boundary(pid))
                if tris:
                    batches["tris"].append(
                        (batch_for_shader(point_shader, "TRIS", {"pos": tris}),
                         COL_FILL_BAD))
        for pid in extras.get("background", []):
            if pid in graph.patches:
                tris = _fan(graph.patch_boundary(pid))
                if tris:
                    batches["tris"].append(
                        (batch_for_shader(point_shader, "TRIS", {"pos": tris}),
                         COL_FILL_BG))
        for pid, patch in graph.patches.items():
            if patch.fill == "hole":
                pts = graph.patch_boundary(pid)
                pairs = []
                for i in range(len(pts)):
                    pairs.append(tuple(pts[i]))
                    pairs.append(tuple(pts[(i + 1) % len(pts)]))
                if pairs:
                    batches["lines"].append(
                        (batch_for_shader(line_shader, "LINES",
                                          {"pos": pairs}),
                         COL_HOLE_EDGE, 1.6))

    # subdivision ticks: where the verts will land, before any rebuild
    if extras.get("show_ticks", True):
        from ..core.surface import resample
        ticks = []
        for arc in graph.arcs.values():
            n = arc.n
            if not n or n < 2 or len(arc.path) < 2:
                continue
            pts = resample(np.asarray(arc.path, dtype=float), int(n))
            for q in pts[1:-1]:
                ticks.append(tuple(q))
        if ticks:
            batches["points"].append(
                (batch_for_shader(point_shader, "POINTS", {"pos": ticks}),
                 COL_TICK, 3.5))

    # the symmetry seam itself, so the mirror line is visible where no arcs run
    seam = extras.get("seam_curve")
    if seam is not None and len(seam) >= 2:
        pairs = []
        pts = np.asarray(seam, dtype=float).reshape(-1, 3)
        finite = np.isfinite(pts).all(axis=1)
        for i in range(len(pts) - 1):
            if finite[i] and finite[i + 1]:
                pairs.append(tuple(pts[i]))
                pairs.append(tuple(pts[i + 1]))
        if pairs:
            batches["lines"].append(
                (batch_for_shader(line_shader, "LINES", {"pos": pairs}),
                 COL_SEAM_CURVE, 1.4))

    palette = dict(COL_ARC)
    palette["pinned"] = COL_PINNED
    for kind, pairs in _segments(graph).items():
        if pairs:
            batches["lines"].append(
                (batch_for_shader(line_shader, "LINES", {"pos": pairs}),
                 palette.get(kind, COL_ARC["flow"]),
                 3.8 if kind == "pinned" else 3.0)
            )
    # Arcs with no symmetry partner: the ones that quietly split a "mirrored"
    # layout into two independent halves. Drawn in warning orange so an
    # unpaired region is visible before its solve behaviour gets confusing.
    if axis != "NONE":
        from ..core.symmetry import unpaired_arcs
        pairs = []
        for aid in unpaired_arcs(graph, axis):
            path = np.asarray(graph.arcs[aid].path, dtype=float)
            for i in range(len(path) - 1):
                pairs.append(tuple(path[i]))
                pairs.append(tuple(path[i + 1]))
        if pairs:
            batches["lines"].append(
                (batch_for_shader(line_shader, "LINES", {"pos": pairs}),
                 COL_UNPAIRED, 3.6))

    arc = graph.arcs.get(int(active)) if active is not None else None
    if arc is not None and len(arc.path) >= 2:
        pairs = []
        path = np.asarray(arc.path, dtype=float)
        for i in range(len(path) - 1):
            pairs.append(tuple(path[i]))
            pairs.append(tuple(path[i + 1]))
        batches["lines"].append(
            (batch_for_shader(line_shader, "LINES", {"pos": pairs}),
             COL_ACTIVE, 4.5))

    bad = _bad_patch_loops(graph, bad_ids)
    if bad:
        batches["lines"].append(
            (batch_for_shader(line_shader, "LINES", {"pos": bad}), COL_BAD, 5.0)
        )

    val = graph.valence()
    plain = [tuple(n.co) for nid, n in graph.nodes.items() if val.get(nid, 0) == 2]
    corner = [tuple(n.co) for nid, n in graph.nodes.items() if val.get(nid, 0) != 2]
    if plain:
        batches["points"].append(
            (batch_for_shader(point_shader, "POINTS", {"pos": plain}),
             COL_NODE, 8.0))
    if corner:
        batches["points"].append(
            (batch_for_shader(point_shader, "POINTS", {"pos": corner}),
             COL_CORNER, 12.0))
    return line_shader, point_shader, batches


def draw():
    ctx = bpy.context
    st = getattr(ctx.scene, "nx_loom", None)
    if st is None or not st.show_overlay:
        return
    obj = getattr(ctx, "active_object", None)
    graph = _graph_of(obj)
    if graph is None:
        return

    bad_ids = set(obj.get("nx_loom_bad_patches", []) or [])
    active = obj.get("nx_loom_active_arc")
    axis = getattr(st, "symmetry_axis", "NONE")
    extras = {
        "show_fills": bool(getattr(st, "show_fills", True)),
        "show_ticks": bool(getattr(st, "show_ticks", True)),
        "background": list(obj.get("nx_loom_background", []) or []),
        "seam_curve": (list(obj.get("nx_loom_seam_curve", []) or [])
                       if axis != "NONE" else None),
    }
    blob = obj.get(GRAPH_KEY, "")
    key = (obj.name, hash(blob), len(blob),
           tuple(sorted(bad_ids)), active, axis,
           extras["show_fills"], extras["show_ticks"],
           tuple(extras["background"]),
           len(extras["seam_curve"] or []))
    if _cache["key"] != key:
        try:
            _cache["batches"] = _build(graph, bad_ids, active, axis, extras)
            _cache["key"] = key
        except Exception:
            return
    line_shader, point_shader, batches = _cache["batches"]

    # A draw handler can be called with no region bound (offscreen rendering,
    # restricted contexts). viewportSize only scales line width, so a sane
    # default is better than raising inside a draw callback.
    region = getattr(ctx, "region", None)
    view_size = (region.width, region.height) if region else (1920.0, 1080.0)
    gpu.state.blend_set("ALPHA")
    try:
        ui = float(bpy.context.preferences.system.ui_scale) or 1.0
    except Exception:
        ui = 1.0

    def _pass(alpha):
        point_shader.bind()
        for batch, color in batches.get("tris", []):
            if alpha < 1.0:
                continue        # washes render front-side only
            point_shader.uniform_float("color", color)
            batch.draw(point_shader)
        line_shader.bind()
        line_shader.uniform_float("viewportSize", view_size)
        for batch, color, width in batches["lines"]:
            line_shader.uniform_float("lineWidth", width * ui)
            line_shader.uniform_float(
                "color", (color[0], color[1], color[2], color[3] * alpha))
            batch.draw(line_shader)
        point_shader.bind()
        for batch, color, size in batches["points"]:
            gpu.state.point_size_set(size * ui)
            point_shader.uniform_float(
                "color", (color[0], color[1], color[2], color[3] * alpha))
            batch.draw(point_shader)

    # The arcs lie exactly ON the surfaces in the depth buffer — they are the
    # sculpt's skin and the generated mesh's patch borders — so an unbiased
    # depth test is a coin flip and half the front of the layout renders at
    # back-side strength. Pull the overlay toward the camera in NDC before
    # testing; both passes share the bias, so the front/back split stays
    # consistent.
    proj = gpu.matrix.get_projection_matrix().copy()
    proj[2][3] -= 1e-3
    gpu.matrix.push_projection()
    gpu.matrix.load_projection_matrix(proj)
    try:
        if st.overlay_xray:
            gpu.state.depth_test_set("LESS_EQUAL")
            _pass(1.0)
            gpu.state.depth_test_set("GREATER")
            _pass(0.35)
            gpu.state.depth_test_set("NONE")
        else:
            gpu.state.depth_test_set("LESS_EQUAL")
            _pass(1.0)
    finally:
        gpu.matrix.pop_projection()

    path = _preview["path"]
    if path is not None and len(path) >= 2:
        pairs = []
        for i in range(len(path) - 1):
            pairs.append(tuple(path[i]))
            pairs.append(tuple(path[i + 1]))
        line_shader.bind()
        line_shader.uniform_float("viewportSize", view_size)
        line_shader.uniform_float("lineWidth", 3.0)
        line_shader.uniform_float("color", COL_PREVIEW)
        batch_for_shader(line_shader, "LINES", {"pos": pairs}).draw(line_shader)

    arc = _hover["arc"]
    if arc is not None and len(arc) >= 2:
        pairs = []
        for i in range(len(arc) - 1):
            pairs.append(tuple(arc[i]))
            pairs.append(tuple(arc[i + 1]))
        line_shader.bind()
        line_shader.uniform_float("viewportSize", view_size)
        line_shader.uniform_float("lineWidth", 6.0)
        line_shader.uniform_float("color", COL_HOVER)
        batch_for_shader(line_shader, "LINES", {"pos": pairs}).draw(line_shader)

    if _hover["node"] is not None:
        point_shader.bind()
        gpu.state.point_size_set(15.0)
        point_shader.uniform_float("color", COL_HOVER)
        batch_for_shader(point_shader, "POINTS",
                         {"pos": [tuple(_hover["node"])]}).draw(point_shader)

    if _hover["seam"] is not None:
        point_shader.bind()
        gpu.state.point_size_set(16.0)
        point_shader.uniform_float("color", COL_SEAM)
        batch_for_shader(point_shader, "POINTS",
                         {"pos": [tuple(_hover["seam"])]}).draw(point_shader)

    marks = [p for p in (_preview["snap"], _preview["anchor"]) if p is not None]
    if marks:
        point_shader.bind()
        gpu.state.point_size_set(12.0)
        point_shader.uniform_float("color", COL_SNAP)
        batch_for_shader(point_shader, "POINTS",
                         {"pos": [tuple(m) for m in marks]}).draw(point_shader)

    gpu.state.point_size_set(1.0)
    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")


def draw_text():
    """Loop counts, drawn in screen space.

    The global integer solve is the cleverest thing here, and while it is only
    a density slider it is invisible. Showing the number an arc carries — and
    marking the ones being held — turns it into something you can reason about
    rather than trust.
    """
    ctx = bpy.context
    st = getattr(ctx.scene, "nx_loom", None)
    if st is None or not st.show_overlay:
        return
    obj = getattr(ctx, "active_object", None)
    graph = _graph_of(obj)
    if graph is None:
        return
    show_counts = bool(getattr(st, "show_counts", False))
    region = getattr(ctx, "region", None)
    rv3d = getattr(getattr(ctx, "space_data", None), "region_3d", None)
    if region is None or rv3d is None:
        return

    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector

    hover = _hover["arc"]
    active = obj.get("nx_loom_active_arc")
    try:
        blf.size(0, 12)
    except Exception:
        return

    if getattr(st, "show_legend", False):
        _draw_legend(region)

    if _hover["seam"] is not None:
        p2d = location_3d_to_region_2d(region, rv3d,
                                       Vector(tuple(_hover["seam"])))
        if p2d is not None:
            blf.color(0, *COL_SEAM)
            blf.position(0, p2d.x + 10, p2d.y + 10, 0)
            blf.draw(0, "mid")
    if not show_counts:
        return
    for arc in graph.arcs.values():
        pinned = bool(arc.n_lock)
        path = np.asarray(arc.path, dtype=float)
        if len(path) < 2 or arc.n is None:
            continue
        near_hover = (hover is not None and len(hover) == len(path)
                      and bool(np.allclose(hover, path)))
        is_active = (active is not None and int(active) == arc.id)
        if not (pinned or near_hover or is_active):
            continue
        p2d = location_3d_to_region_2d(region, rv3d,
                                       Vector(tuple(path[len(path) // 2])))
        if p2d is None:
            continue
        blf.color(0, *(COL_ACTIVE if is_active else
                       (COL_PINNED if pinned else COL_HOVER)))
        blf.position(0, p2d.x + 6, p2d.y + 6, 0)
        blf.draw(0, f"{arc.n}" + ("*" if pinned else ""))


LEGEND = (
    ("flow", COL_ARC["flow"]),
    ("crease", COL_ARC["crease"]),
    ("boundary", COL_ARC["boundary"]),
    ("seam", COL_ARC["seam"]),
    ("pinned", COL_PINNED),
    ("selected", COL_ACTIVE),
    ("unpaired", COL_UNPAIRED),
    ("failed patch", COL_FILL_BAD[:3] + (1.0,)),
    ("hole", COL_HOLE_EDGE[:3] + (1.0,)),
    ("mid / snap", COL_SEAM),
)


def _draw_legend(region):
    """The colour key. Every one of these colours means something that only
    the author used to know."""
    x, y0 = 14.0, 14.0
    blf.size(0, 11)
    for i, (label, col) in enumerate(reversed(LEGEND)):
        y = y0 + i * 16
        blf.color(0, col[0], col[1], col[2], 1.0)
        blf.position(0, x, y, 0)
        blf.draw(0, "\u25a0")
        blf.color(0, 0.85, 0.85, 0.88, 0.9)
        blf.position(0, x + 16, y, 0)
        blf.draw(0, label)


def enable():
    global _handle, _handle_px
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(draw, (), "WINDOW",
                                                         "POST_VIEW")
    if _handle_px is None:
        _handle_px = bpy.types.SpaceView3D.draw_handler_add(draw_text, (),
                                                            "WINDOW", "POST_PIXEL")
    _tag_redraw()


def disable():
    global _handle, _handle_px
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
    if _handle_px is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle_px, "WINDOW")
        _handle_px = None
    _tag_redraw()


def register():
    enable()


def unregister():
    disable()
    _cache["key"] = None
    _hover["node"] = None
    _hover["arc"] = None
    _hover["seam"] = None
