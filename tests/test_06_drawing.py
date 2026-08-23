"""Drawing a layout from nothing.

Every stroke here goes through the same functions the mouse does — the modal
operator only converts mouse positions into rays, and these tests supply the
rays directly. If this passes, the tool works; what is left in the operator is
plumbing.
"""

import bmesh
import bpy
import numpy as np

from nx_loom.core.authoring import (dissolve_node, move_node, nearest_node,
                                    nearest_on_arc, remove_arc)
from nx_loom.core.graph import GRAPH_KEY
from nx_loom.core.picking import trace_rays
from nx_loom.core.surface import Surface
from nx_loom.ops.draw import commit_arc
from nx_loom.ops.layout import get_graph, rebuild_object, set_graph

R = 1.0
SNAP = 0.10
MIN_STEP = 0.008


def _rays(points):
    """Rays aimed at the sphere from outside — what a viewport click produces."""
    out = []
    for p in points:
        p = np.asarray(p, dtype=float)
        out.append((p * 3.0, -p))
    return out


def _arc_points(a, b, n=14):
    """Great-circle samples between two unit vectors."""
    a = np.asarray(a, dtype=float) / np.linalg.norm(a)
    b = np.asarray(b, dtype=float) / np.linalg.norm(b)
    omega = np.arccos(np.clip(a @ b, -1.0, 1.0))
    if omega > np.pi - 1e-6:
        raise ValueError("antipodal endpoints have no unique great circle")
    pts = []
    for k in range(n + 1):
        t = k / n
        if omega < 1e-9:
            pts.append(a * R)
            continue
        w = (np.sin((1 - t) * omega) * a + np.sin(t * omega) * b) / np.sin(omega)
        pts.append(w / np.linalg.norm(w) * R)
    return pts


def _setup():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=R)
    src = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.25
    st.relax_iters = 8
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object
    return src, obj, Surface(src, bpy.context.evaluated_depsgraph_get())


def _draw(graph, surface, a, b, start_node=None, arc_type="flow"):
    return commit_arc(graph, surface, _rays(_arc_points(a, b)),
                      SNAP, MIN_STEP, arc_type=arc_type, start_node=start_node)


def _survey(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    out = {
        "V": len(bm.verts), "E": len(bm.edges), "F": len(bm.faces),
        "nonquad": sum(1 for f in bm.faces if len(f.verts) != 4),
        "nm": sum(1 for e in bm.edges if len(e.link_faces) > 2),
        "boundary": sum(1 for e in bm.edges if len(e.link_faces) == 1),
        "loose": sum(1 for v in bm.verts if not v.link_faces),
    }
    out["euler"] = out["V"] - out["E"] + out["F"]
    bm.free()
    return out


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # -- rays land on the surface at all
    src, obj, surface = _setup()
    path = trace_rays(surface, _rays(_arc_points((1, 0, 0), (0, 1, 0))))
    on_sphere = np.abs(np.linalg.norm(path, axis=1) - R).max() if len(path) else 9
    out.append(("rays trace onto the surface", len(path) >= 10 and on_sphere < 0.02,
                f"{len(path)} pts, max radial error {on_sphere:.4f}"))

    # -- draw an octahedral layout: equator ring, then spokes to both poles
    graph = get_graph(obj)
    N, S = (0, 0, 1), (0, 0, -1)
    eq = [(1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)]

    for i in range(4):
        _draw(graph, surface, eq[i], eq[(i + 1) % 4])
    out.append(("equator ring: 4 arcs, 4 shared nodes",
                len(graph.arcs) == 4 and len(graph.nodes) == 4,
                f"{len(graph.nodes)}n {len(graph.arcs)}a"))

    for pole in (N, S):
        for i in range(4):
            _draw(graph, surface, eq[i], pole)
    out.append(("spokes snap to the poles and the ring",
                len(graph.arcs) == 12 and len(graph.nodes) == 6,
                f"{len(graph.nodes)}n {len(graph.arcs)}a"))

    rep = graph.discover_patches(normal_at=surface.normal_at)
    sides = {}
    for p in graph.patches.values():
        sides[len(p.sides)] = sides.get(len(p.sides), 0) + 1
    out.append(("8 triangle patches discovered", sides == {3: 8}, f"{sides} {rep}"))

    set_graph(obj, graph)
    build = rebuild_object(obj, bpy.context)
    st = _survey(obj)
    ok = (st["euler"] == 2 and st["nonquad"] == 0 and st["nm"] == 0
          and st["boundary"] == 0 and st["loose"] == 0 and st["F"] > 0)
    out.append(("drawn layout builds a closed all-quad mesh", ok, str(st)))

    dev = 0.0
    if st["V"]:
        P = np.array([tuple(obj.matrix_world @ v.co) for v in obj.data.vertices])
        dev = float(np.abs(np.linalg.norm(P, axis=1) - R).max())
    out.append(("generated verts lie on the sphere", dev < 0.02, f"max {dev:.4f}"))

    # -- density still re-grids a hand-drawn layout
    counts = []
    for te in (0.5, 0.25, 0.12):
        bpy.context.scene.nx_loom.target_edge = te
        rebuild_object(obj, bpy.context)
        s2 = _survey(obj)
        counts.append(s2["F"])
        if not (s2["euler"] == 2 and s2["nonquad"] == 0 and s2["nm"] == 0):
            out.append((f"re-grid @ {te}", False, str(s2)))
            break
    else:
        out.append(("density re-grids the drawn layout",
                    counts == sorted(counts) and counts[0] < counts[-1], str(counts)))

    # -- drawing into the middle of an arc splits it (a T-junction you can draw)
    src, obj, surface = _setup()
    graph = get_graph(obj)
    _draw(graph, surface, (1, 0, 0), (0, 1, 0))
    before = len(graph.arcs)
    mid = np.array([1.0, 1.0, 0.0]); mid = mid / np.linalg.norm(mid) * R
    res = _draw(graph, surface, (0, 0, 1), mid)   # lands mid-way along the first arc
    out.append(("crossing arc is split into a T-junction",
                before == 1 and len(graph.arcs) == 3 and len(graph.nodes) == 4
                and res is not None,
                f"{before} -> {len(graph.arcs)} arcs, {len(graph.nodes)} nodes"))
    tj = nearest_node(graph, mid, 0.15)
    val = graph.valence()
    out.append(("the split node is a real 3-way junction",
                tj is not None and val.get(tj[0]) == 3,
                f"valence {val.get(tj[0]) if tj else None}"))

    # -- snapping reuses a node instead of stacking a second one on top
    n_before, a_before = len(graph.nodes), len(graph.arcs)
    _draw(graph, surface, (0, 0, 1), (0, -1, 0))     # starts at the existing pole
    out.append(("second stroke reuses the existing pole node",
                len(graph.nodes) == n_before + 1 and len(graph.arcs) == a_before + 1,
                f"{n_before}n/{a_before}a -> {len(graph.nodes)}n/{len(graph.arcs)}a"))

    # -- erase and dissolve
    src, obj, surface = _setup()
    graph = get_graph(obj)
    for i in range(4):
        _draw(graph, surface, eq[i], eq[(i + 1) % 4])
    remove_arc(graph, sorted(graph.arcs)[0])
    out.append(("erase drops the arc and prunes orphans",
                len(graph.arcs) == 3 and len(graph.nodes) == 4,
                f"{len(graph.nodes)}n {len(graph.arcs)}a"))

    src, obj, surface = _setup()
    graph = get_graph(obj)
    _draw(graph, surface, (1, 0, 0), (0, 1, 0))
    _draw(graph, surface, (0, 1, 0), (-1, 0, 0))
    mid_node = nearest_node(graph, np.array([0.0, 1.0, 0.0]) * R, 0.2)
    merged = dissolve_node(graph, mid_node[0], surface)
    out.append(("dissolve merges two arcs back into one",
                merged is not None and len(graph.arcs) == 1 and len(graph.nodes) == 2,
                f"{len(graph.nodes)}n {len(graph.arcs)}a"))

    # -- moving a node drags every arc end with it
    src, obj, surface = _setup()
    graph = get_graph(obj)
    _draw(graph, surface, (1, 0, 0), (0, 1, 0))
    _draw(graph, surface, (0, 1, 0), (0, 0, 1))
    nid = nearest_node(graph, np.array([0.0, 1.0, 0.0]) * R, 0.2)[0]
    target = np.array([0.0, 0.9, 0.4])
    target = target / np.linalg.norm(target) * R
    move_node(graph, nid, target, surface)
    attached = []
    for arc in graph.arcs.values():
        if arc.a == nid:
            attached.append(np.allclose(arc.path[0], target))
        if arc.b == nid:
            attached.append(np.allclose(arc.path[-1], target))
    out.append(("move_node drags both arc ends", len(attached) == 2 and all(attached),
                str(attached)))
    out.append(("moved node stays pinned to the surface",
                graph.nodes[nid].pin is not None, ""))

    out += run_crossings()
    return out


def run_crossings():
    """A line drawn through another line connects to it.

    Without the shared junction the layout is non-planar and discovery
    mis-traverses it — the drawn diagonal in the report just floated over the
    patch's middle arc.
    """
    import bmesh

    from nx_loom.ops.layout import rebuild_object, set_graph

    out = []

    # unit-level, flat: one segment crossed by another -> an X with a shared
    # valence-4 junction, both originals split
    src, obj, surface = _setup()
    graph = get_graph(obj)
    _draw(graph, surface, (1, 0, 0), (0, 1, 0))
    n_nodes, n_arcs = len(graph.nodes), len(graph.arcs)
    res = _draw(graph, surface, (1, 1, -1), (1, 1, 1))   # crosses the first
    out.append(("drawing through an arc yields four arcs",
                res is not None and len(graph.arcs) == n_arcs + 3
                and len(graph.nodes) == n_nodes + 3,
                f"{len(graph.arcs)} arcs, {len(graph.nodes)} nodes"))
    val = graph.valence()
    junctions = [n for n, v in val.items() if v == 4]
    out.append(("they meet at one valence-4 junction", len(junctions) == 1,
                f"valences {sorted(val.values())}"))

    # the reported case: a quad region with a mid arc, then a diagonal drawn
    # corner-to-corner straight through it
    src, obj, surface = _setup()
    graph = get_graph(obj)
    corners = [(1, 0, 0), (0, 1, 0), (-0.4, 0.4, 0.8), (0.4, -0.4, 0.8)]
    ids = {}
    for i in range(4):
        _draw(graph, surface, corners[i], corners[(i + 1) % 4])
    # the mid arc across the region
    m0 = np.array([0.7, -0.2, 0.4])
    m1 = np.array([-0.2, 0.7, 0.4])
    m0, m1 = m0 / np.linalg.norm(m0), m1 / np.linalg.norm(m1)
    _draw(graph, surface, tuple(m0), tuple(m1))
    arcs_before = len(graph.arcs)
    # the diagonal through it
    res = _draw(graph, surface, corners[0], corners[2])
    out.append(("the diagonal connects to the arc it crosses",
                res is not None and len(graph.arcs) >= arcs_before + 3,
                f"{arcs_before} -> {len(graph.arcs)} arcs"))
    val = graph.valence()
    out.append(("with a real junction where they meet",
                any(v >= 4 for v in val.values()),
                f"max valence {max(val.values())}"))

    set_graph(obj, graph)
    rep = rebuild_object(obj, bpy.context)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    nm = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    nq = sum(1 for f in bm.faces if len(f.verts) != 4)
    F = len(bm.faces)
    bm.free()
    out.append(("and the crossed region builds clean quads",
                F > 0 and nm == 0 and nq == 0 and not rep["unsatisfied_patches"],
                f"{F} faces, nm={nm}, unsat {rep['unsatisfied_patches']}"))

    # a line through TWO parallels becomes three arcs with two junctions
    src, obj, surface = _setup()
    graph = get_graph(obj)
    _draw(graph, surface, (1, 0, -0.35), (0, 1, -0.35))
    _draw(graph, surface, (1, 0, 0.35), (0, 1, 0.35))
    res = _draw(graph, surface, (0.9, 0.1, -0.7), (0.1, 0.9, 0.7))
    val = graph.valence()
    out.append(("crossing two parallels makes two junctions",
                sum(1 for v in val.values() if v == 4) == 2
                and len(graph.arcs) == 7,
                f"{len(graph.arcs)} arcs, valences {sorted(val.values())}"))

    # ending ON an arc is still the anchors' business — exactly one T node
    src, obj, surface = _setup()
    graph = get_graph(obj)
    _draw(graph, surface, (1, 0, 0), (0, 1, 0))
    mid = np.array([1.0, 1.0, 0.0])
    mid = mid / np.linalg.norm(mid)
    _draw(graph, surface, (0.6, 0.6, 0.8), tuple(mid))
    val = graph.valence()
    out.append(("a T-junction end is not double-cut",
                len(graph.arcs) == 3 and sorted(val.values()) == [1, 1, 1, 3],
                f"{len(graph.arcs)} arcs, valences {sorted(val.values())}"))
    return out
