"""Working near the symmetry line without fighting it.

The mirrored half used to be a trap: erased mirrors regenerated (zombies),
dragged mirrored nodes reverted on a later edit, and strokes attached to
derived geometry decayed into orphaned stubs. Every gesture now redirects to
the authored source, so acting on either half is acting on the document.
"""

import bpy
import numpy as np

from nx_loom.core import authoring as A
from nx_loom.core.surface import Surface
from nx_loom.core.symmetry import source_arc, source_node, unpaired_arcs
from nx_loom.ops.draw import commit_arc, commit_path
from nx_loom.ops.layout import get_graph, rebuild_object, set_graph

NP, SP, PX, FY, BY = (0, 0, 1), (0, 0, -1), (1, 0, 0), (0, 1, 0), (0, -1, 0)


def _gc(a, b, n=12):
    a = np.array(a, float) / np.linalg.norm(a)
    b = np.array(b, float) / np.linalg.norm(b)
    om = np.arccos(np.clip(a @ b, -1, 1))
    return [(np.sin((1 - t) * om) * a + np.sin(t * om) * b) / np.sin(om)
            for t in [k / n for k in range(n + 1)]]


def _rays(P):
    return [(np.array(p) * 3.0, -np.array(p)) for p in P]


def _setup():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    src = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.3
    st.relax_iters = 4
    st.symmetry_axis = "X"
    st.symmetry_tolerance = 0.02
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object
    surf = Surface(src, bpy.context.evaluated_depsgraph_get())
    g = get_graph(obj)
    for a, b in ((NP, FY), (FY, SP), (SP, BY), (BY, NP),
                 (NP, PX), (PX, SP), (FY, PX), (PX, BY)):
        commit_arc(g, surf, _rays(_gc(a, b)), 0.08, 0.02)
    set_graph(obj, g)
    rebuild_object(obj, bpy.context)
    rebuild_object(obj, bpy.context)
    return obj, surf


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # -- no zombie arcs: erasing through the source kills both halves for good
    obj, surf = _setup()
    g = get_graph(obj)
    mirror = next(a for a, arc in g.arcs.items() if arc.mirror_of is not None)
    src_id = source_arc(g, mirror)
    out.append(("a derived arc resolves to its source",
                src_id != mirror and g.arcs[src_id].mirror_of is None, ""))
    n_before = len(g.arcs)
    A.remove_arc(g, src_id)
    set_graph(obj, g)
    rebuild_object(obj, bpy.context)
    g = get_graph(obj)
    out.append(("erasing via the source removes both halves",
                src_id not in g.arcs and mirror not in g.arcs
                and len(g.arcs) == n_before - 2,
                f"{n_before} -> {len(g.arcs)}"))
    rebuild_object(obj, bpy.context)
    g = get_graph(obj)
    out.append(("and they stay gone — no zombies",
                src_id not in g.arcs and mirror not in g.arcs,
                f"{len(g.arcs)} arcs"))

    # -- a mirrored-node drag persists: driving the source with the reflected
    # position is what the modal does
    obj, surf = _setup()
    g = get_graph(obj)
    mnode = next(n for n, nd in g.nodes.items() if nd.mirror_of is not None
                 and abs(nd.co[0]) > 0.3)
    snode = source_node(g, mnode)
    target = np.asarray(g.nodes[mnode].co, float) + np.array([0.0, 0.15, 0.1])
    target /= np.linalg.norm(target)
    reflected = target.copy()
    reflected[0] *= -1.0
    A.move_node(g, snode, reflected, surf)
    set_graph(obj, g)
    rebuild_object(obj, bpy.context)
    rebuild_object(obj, bpy.context)          # a later rebuild must not revert
    g = get_graph(obj)
    live = next((n for n, nd in g.nodes.items()
                 if nd.mirror_of == snode), None)
    ok = live is not None and float(np.linalg.norm(
        np.asarray(g.nodes[live].co, float) - target)) < 0.05
    out.append(("dragging the mirrored half sticks through rebuilds", ok,
                "" if not ok else "mirror follows the moved source"))

    # -- a stroke attached to the mirrored half lands authored via reflection
    obj, surf = _setup()
    g = get_graph(obj)
    st = bpy.context.scene.nx_loom
    # a stroke on the -x half, ending on the mirrored spoke
    stroke = np.array([p for p in _gc((-0.5, 0.75, 0.44), (-1, 0, 0))])
    n_arcs = len(g.arcs)
    res = commit_path(g, surf, stroke, 0.08, 0.02, plane=(0, 0.02))
    out.append(("the stroke commits", res is not None, ""))
    new_arcs = [a for a in g.arcs if a not in range(n_arcs)]
    authored_new = [a for a, arc in g.arcs.items()
                    if arc.mirror_of is None and a >= n_arcs]
    on_positive = all(np.asarray(g.arcs[a].path)[:, 0].mean() > -0.02
                      for a in authored_new)
    out.append(("it lands on the authored side, reflected",
                len(authored_new) >= 1 and on_positive,
                f"{len(authored_new)} new authored arcs"))
    set_graph(obj, g)
    rebuild_object(obj, bpy.context)
    g = get_graph(obj)
    out.append(("both halves carry it after sync, nothing unpaired",
                len(unpaired_arcs(g, "X", st.symmetry_tolerance)) == 0,
                f"{len(unpaired_arcs(g, 'X', st.symmetry_tolerance))} unpaired"))

    # -- the seam snap has an off switch and a per-click bypass
    out.append(("seam snap is toggleable",
                hasattr(st, "seam_snap") and st.seam_snap, ""))
    import inspect

    from nx_loom.ops import draw as draw_ops
    src_txt = inspect.getsource(draw_ops)
    out.append(("Ctrl-click bypasses the snap in the draw modal",
                "_ctrl_click" in src_txt and "event.ctrl" in src_txt, ""))
    out.append(("the toggle gates the plane helper",
                'getattr(st, "seam_snap", True)' in src_txt, ""))
    return out
