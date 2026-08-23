"""Exercise the parts that only exist in a real viewport: the GPU overlay and
the modal invoke path. Run under xvfb — never on the user's display.

Verifies by pixels, not by absence of exceptions: the overlay is only working
if the accent colour actually reaches the framebuffer.
"""

import os
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
OUT = os.environ.get("NXL_SHOT", "/tmp/nxl_shot.png")

import bpy  # noqa: E402

import nx_loom  # noqa: E402
from nx_loom.core.picking import trace_rays  # noqa: E402
from nx_loom.core.surface import Surface  # noqa: E402
from nx_loom.ops.draw import commit_arc  # noqa: E402
from nx_loom.ops.layout import get_graph, rebuild_object, set_graph  # noqa: E402

RESULTS = []


def check(label, ok, msg=""):
    RESULTS.append((label, bool(ok), str(msg)))
    print(f"   {'ok  ' if ok else 'FAIL'} {label}" + (f"  [{msg}]" if msg else ""))


def view3d():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "WINDOW":
                        return {"window": win, "screen": win.screen, "area": area,
                                "region": region, "space_data": area.spaces.active}
    return None


def arc_points(a, b, n=14):
    a = np.asarray(a, float) / np.linalg.norm(a)
    b = np.asarray(b, float) / np.linalg.norm(b)
    om = np.arccos(np.clip(a @ b, -1, 1))
    pts = []
    for k in range(n + 1):
        t = k / n
        w = (np.sin((1 - t) * om) * a + np.sin(t * om) * b) / np.sin(om)
        pts.append(w / np.linalg.norm(w))
    return pts


def rays(pts):
    return [(np.asarray(p) * 3.0, -np.asarray(p)) for p in pts]


def build_scene(fresh=True):
    if fresh:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    src = bpy.context.view_layer.objects.active
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.3
    st.relax_iters = 8
    st.show_overlay = True
    st.overlay_xray = True
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.view_layer.objects.active
    surf = Surface(src, bpy.context.evaluated_depsgraph_get())
    graph = get_graph(obj)
    eq = [(1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)]
    for i in range(4):
        commit_arc(graph, surf, rays(arc_points(eq[i], eq[(i + 1) % 4])), 0.1, 0.008)
    for pole in ((0, 0, 1), (0, 0, -1)):
        for i in range(4):
            commit_arc(graph, surf, rays(arc_points(eq[i], pole)), 0.1, 0.008)
    graph.discover_patches(normal_at=surf.normal_at)
    set_graph(obj, graph)
    rebuild_object(obj, bpy.context)
    src.hide_set(True)
    return src, obj, graph


def stage_two():
    ctx = view3d()
    if ctx is None:
        check("a 3D viewport exists", False, "no VIEW_3D area")
        return finish()
    check("a 3D viewport exists", True)

    with bpy.context.temp_override(**ctx):
        ctx["space_data"].shading.type = "SOLID"
        bpy.ops.view3d.view_axis(type="FRONT")
        bpy.ops.view3d.view_all()

        try:
            bpy.ops.wm.tool_set_by_id(name="nxloom.draw_tool")
            active = bpy.context.workspace.tools.from_space_view3d_mode(
                "OBJECT", create=False)
            check("Loom Draw tool activates from the toolbar",
                  active is not None and active.idname == "nxloom.draw_tool",
                  active.idname if active else "none")
        except Exception as e:
            check("Loom Draw tool activates from the toolbar", False, repr(e))

        # the modal invoke path: Surface build, poll, cursor, handler add
        try:
            ok_poll = bpy.ops.nxloom.draw_arc.poll()
            check("draw_arc polls true in the viewport", ok_poll)
        except Exception as e:
            check("draw_arc polls true in the viewport", False, repr(e))

        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=4)
        _offscreen_check(ctx)

    _deferred_check(ctx)


def _render(ctx, size):
    """Render the overlay into an offscreen buffer and read the pixels back."""
    import gpu

    from nx_loom.ui import overlay

    rv3d = ctx["space_data"].region_3d
    try:
        off = gpu.types.GPUOffScreen(size, size)
    except Exception:
        return None
    try:
        with off.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.0, 0.0, 0.0, 1.0), depth=1.0)
            with gpu.matrix.push_pop():
                gpu.matrix.load_matrix(rv3d.view_matrix)
                gpu.matrix.load_projection_matrix(rv3d.window_matrix)
                overlay.draw()
            buf = fb.read_color(0, 0, size, size, 3, 0, "FLOAT")
        buf.dimensions = size * size * 3
        return np.array(buf.to_list(), dtype=float).reshape(-1, 3)
    except Exception:
        return None
    finally:
        off.free()


def _offscreen_check(ctx):
    """Render the overlay into an offscreen buffer and count its pixels.

    `screen.screenshot` reads the window's front buffer, which is empty under
    llvmpipe — it returns a fully black image whether the overlay drew or not,
    so it cannot tell us anything. An offscreen framebuffer renders the same
    batches through the same shaders and can actually be read back.
    """
    from nx_loom.ops.layout import get_graph
    from nx_loom.ui import overlay

    size = 600
    px = _render(ctx, size)
    if px is None:
        check("overlay renders into an offscreen buffer", False,
              "offscreen render failed")
        return

    lit = int((px.max(axis=1) > 0.05).sum())
    violet = int((((px[:, 2] > 0.45) & (px[:, 2] - px[:, 1] > 0.15)
                   & (px[:, 0] - px[:, 1] > 0.02))).sum())
    white = int(((px > 0.9).all(axis=1)).sum())
    amber_idle = int(((px[:, 0] > 0.8) & (px[:, 1] > 0.6) & (px[:, 2] < 0.4)).sum())
    check("overlay draws pixels at all", lit > 200, f"{lit} lit of {len(px)}")
    check("arcs use the accent colour", violet > 100, f"{violet} violet px")
    check("corner nodes drawn", white > 10, f"{white} near-white px")

    # hovering a node must visibly highlight it
    graph = get_graph(bpy.context.view_layer.objects.active)
    node = next(iter(graph.nodes.values()))
    overlay.set_hover(node=np.asarray(node.co, dtype=float))
    hp = _render(ctx, size)
    overlay.clear_hover()
    if hp is None:
        check("hover highlight renders", False, "offscreen render failed")
        return
    amber = int(((hp[:, 0] > 0.8) & (hp[:, 1] > 0.6) & (hp[:, 2] < 0.4)).sum())
    check("hover highlight renders", amber > amber_idle,
          f"{amber_idle} amber px idle -> {amber} while hovering")

    arc = next(iter(graph.arcs.values()))
    overlay.set_hover(arc=np.asarray(arc.path, dtype=float))
    ap = _render(ctx, size)
    overlay.clear_hover()
    if ap is not None:
        amber_arc = int(((ap[:, 0] > 0.8) & (ap[:, 1] > 0.6)
                         & (ap[:, 2] < 0.4)).sum())
        check("hovering an arc highlights the whole arc",
              amber_arc > amber, f"{amber} (node) -> {amber_arc} (arc) amber px")

    # subdivision ticks: dim grey dots along every arc
    grey = int(((px[:, 0] > 0.3) & (px[:, 0] < 0.65)
                & (np.abs(px[:, 0] - px[:, 1]) < 0.06)
                & (np.abs(px[:, 1] - px[:, 2]) < 0.06)).sum())
    check("subdivision ticks render", grey > 40, f"{grey} tick px")

    # state fill: stage one failing patch and expect a red wash
    obj2 = bpy.context.view_layer.objects.active
    pid = sorted(graph.patches)[0]
    obj2["nx_loom_bad_patches"] = [pid]
    overlay.mark_dirty()
    fp = _render(ctx, size)
    obj2["nx_loom_bad_patches"] = []
    overlay.mark_dirty()
    if fp is not None:
        wash = int(((fp[:, 0] > 0.10) & (fp[:, 0] > fp[:, 1] * 2.0)).sum())
        check("a failing patch renders as a red wash", wash > 150,
              f"{wash} wash px")

    # legend: must render without raising wherever a region exists
    try:
        with bpy.context.temp_override(**ctx):
            overlay.draw_text()
        check("legend and counts draw without raising", True, "")
    except Exception as e:
        check("legend and counts draw without raising", False, repr(e))


def _deferred_check(ctx):
    """The rebuild behind a wheel notch is on a timer. Prove the timer fires.

    If it does not, the pin lands and nothing re-solves — indistinguishable
    from the solver ignoring the pin.
    """
    from nx_loom.ops import draw as draw_ops
    from nx_loom.ops.layout import get_graph, set_graph

    obj = bpy.context.view_layer.objects.active
    graph = get_graph(obj)
    if graph is None or not graph.arcs:
        check("deferred rebuild fires", False, "no layout")
        return

    aid = sorted(graph.arcs)[0]
    before = {a: graph.arcs[a].n for a in graph.arcs}
    graph.arcs[aid].n_lock = int(before[aid]) + 3
    set_graph(obj, graph)
    draw_ops.queue_rebuild(obj, delay=0.05)

    def verify():
        g = get_graph(obj)
        got = g.arcs[aid].n
        want = before[aid] + 3
        check("a queued rebuild actually runs and re-solves", got == want,
              f"arc {aid}: {before[aid]} -> {got} (wanted {want})")
        moved = sum(1 for a in g.arcs if a != aid and g.arcs[a].n != before[a])
        check("and the unpinned arcs re-solve around it", moved > 0,
              f"{moved} other arc(s) adjusted")
        finish()
        return None

    bpy.app.timers.register(verify, first_interval=0.6)


def finish():
    bad = [r for r in RESULTS if not r[1]]
    print(f"\nGUI: {len(RESULTS) - len(bad)} passed, {len(bad)} failed")
    bpy.ops.wm.quit_blender()


def stage_one():
    try:
        nx_loom.register()
    except Exception as e:
        check("addon registers with a UI", False, repr(e))
        return finish()
    check("addon registers with a UI", True)
    ctx = view3d()
    if ctx is None:
        check("a 3D viewport exists at startup", False, "no VIEW_3D area")
        return finish()
    try:
        with bpy.context.temp_override(**ctx):
            bpy.ops.wm.read_factory_settings(use_empty=True)
        ctx = view3d()
        with bpy.context.temp_override(**ctx):
            src, obj, graph = build_scene(fresh=False)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("scene + drawn layout", False, repr(e))
        return finish()
    check("drawn layout built in a UI session", len(graph.patches) == 8,
          f"{len(graph.nodes)}n {len(graph.arcs)}a {len(graph.patches)}p, "
          f"{len(obj.data.polygons)} faces")
    bpy.app.timers.register(stage_two, first_interval=0.6)


bpy.app.timers.register(stage_one, first_interval=0.4)
