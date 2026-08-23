"""Graph edits made by the drawing tools.

Deliberately bpy-free and side-effect-light: the modal operators are a thin
shell over these functions, so the part that can actually be wrong is the part
that can actually be tested.
"""

from __future__ import annotations

import numpy as np

from .graph import Arc, Node


def new_node(graph, co, surface=None, kind="corner"):
    nid = (max(graph.nodes) + 1) if graph.nodes else 0
    node = Node(nid, co, kind=kind)
    if surface is not None:
        node.pin = surface.pin(co)
    graph.nodes[nid] = node
    return nid


def add_arc(graph, a, b, path, surface=None, type="flow", rail="surface"):
    """Add an arc between two existing nodes. Path endpoints are snapped."""
    path = np.asarray(path, dtype=float)
    if len(path) < 2:
        path = np.vstack([graph.nodes[a].co, graph.nodes[b].co])
    path = path.copy()
    path[0] = graph.nodes[a].co
    path[-1] = graph.nodes[b].co
    aid = (max(graph.arcs) + 1) if graph.arcs else 0
    arc = Arc(aid, a, b, path, type=type, rail=rail)
    if surface is not None:
        arc.pins = [surface.pin(p) for p in path]
    graph.arcs[aid] = arc
    return aid


def remove_arc(graph, aid, prune=True):
    arc = graph.arcs.pop(aid, None)
    if arc is None:
        return
    if prune:
        prune_orphan_nodes(graph, (arc.a, arc.b))


def prune_orphan_nodes(graph, candidates=None):
    used = set()
    for arc in graph.arcs.values():
        used.add(arc.a)
        used.add(arc.b)
    pool = graph.nodes.keys() if candidates is None else candidates
    for nid in list(pool):
        if nid in graph.nodes and nid not in used:
            del graph.nodes[nid]


def remove_node(graph, nid):
    """Delete a node outright, along with every arc that touches it.

    Dissolve is for healing a subdivision out of a chain; this is for getting
    rid of something — a stray point placed and abandoned, a dangling stub.
    Without it a loose point cannot be removed at all: it has no arc to erase
    and dissolving needs a valence of exactly 2.
    """
    if nid not in graph.nodes:
        return 0
    doomed = [aid for aid, arc in graph.arcs.items()
              if arc.a == nid or arc.b == nid]
    for aid in doomed:
        del graph.arcs[aid]
    del graph.nodes[nid]
    prune_orphan_nodes(graph)
    return len(doomed)


def split_arc(graph, aid, index, t, surface=None):
    """Split an arc at parameter t inside path segment `index`. Returns node id.

    The two halves keep the original arc's type and rail; a lock is dropped,
    because a count pinned to the whole arc means nothing once it is two arcs.
    """
    arc = graph.arcs[aid]
    path = np.asarray(arc.path, dtype=float)
    index = int(np.clip(index, 0, len(path) - 2))
    t = float(np.clip(t, 0.0, 1.0))
    p = path[index] + (path[index + 1] - path[index]) * t

    nid = new_node(graph, p, surface)
    left = np.vstack([path[: index + 1], p[None, :]])
    right = np.vstack([p[None, :], path[index + 1:]])
    a, b = arc.a, arc.b
    kind, rail = arc.type, arc.rail

    del graph.arcs[aid]
    add_arc(graph, a, nid, left, surface, kind, rail)
    add_arc(graph, nid, b, right, surface, kind, rail)
    return nid


# -- picking ---------------------------------------------------------------

def nearest_node(graph, point, radius):
    point = np.asarray(point, dtype=float)
    best = None
    for nid, node in graph.nodes.items():
        d = float(np.linalg.norm(node.co - point))
        if d <= radius and (best is None or d < best[1]):
            best = (nid, d)
    return best


def nearest_on_arc(graph, point, radius, skip=()):
    """Closest point on any arc polyline. -> (aid, seg_index, t, co, dist)."""
    point = np.asarray(point, dtype=float)
    best = None
    for aid, arc in graph.arcs.items():
        if aid in skip:
            continue
        path = np.asarray(arc.path, dtype=float)
        if len(path) < 2:
            continue
        a = path[:-1]
        ab = path[1:] - a
        denom = np.einsum("ij,ij->i", ab, ab)
        denom[denom < 1e-20] = 1e-20
        t = np.clip(np.einsum("ij,ij->i", point - a, ab) / denom, 0.0, 1.0)
        proj = a + ab * t[:, None]
        d = np.linalg.norm(proj - point, axis=1)
        i = int(np.argmin(d))
        if d[i] <= radius and (best is None or d[i] < best[4]):
            best = (aid, i, float(t[i]), proj[i], float(d[i]))
    return best


def plane_snap(point, plane, surface=None):
    """Clamp a point onto the symmetry plane when it lands within reach.

    ``plane`` is (axis_index, snap_distance) or None. Aiming for the exact
    middle of a symmetric model by eye is otherwise a losing game — the seam
    is a mathematical plane and a hand is not. Returns (point, snapped).
    """
    if plane is None:
        return np.asarray(point, dtype=float), False
    ax, reach = plane
    point = np.asarray(point, dtype=float)
    if abs(float(point[ax])) > reach:
        return point, False
    q = point.copy()
    q[ax] = 0.0
    if surface is not None:
        q = np.asarray(surface.project(q[None])[0], dtype=float)
        q[ax] = 0.0
    return q, True


def resolve_anchor(graph, point, radius, surface=None, plane=None):
    """Where a click lands. -> ("node", nid) after creating/splitting as needed.

    Snapping to an existing arc splits it, which is what makes a T-junction
    something you can just draw into rather than having to plan for. A point
    within reach of the symmetry plane is clamped exactly onto it first.
    """
    point, _ = plane_snap(point, plane, surface)
    hit = nearest_node(graph, point, radius)
    if hit is not None:
        return hit[0], "node"
    hit = nearest_on_arc(graph, point, radius)
    if hit is not None:
        aid, index, t, _, _ = hit
        return split_arc(graph, aid, index, t, surface), "split"
    return new_node(graph, point, surface), "new"


def fair_path(path, iters=12, strength=0.5, project=None):
    """Gently relax a polyline's interior, keeping its endpoints exact.

    A freehand stroke carries every wobble of the hand, and a wobbly arc
    becomes a wobbly edge loop in every mesh generated from it forever. Light
    tangential fairing removes the jitter while leaving the deliberate shape
    of the curve — this is a low-pass filter, not a straightener. Reprojection
    keeps the faired line on the surface rather than cutting corners through
    it.
    """
    p = np.asarray(path, dtype=float).copy()
    if len(p) < 3 or iters <= 0 or strength <= 0.0:
        return p
    w = min(max(strength, 0.0), 1.0) * 0.5
    for it in range(iters):
        p[1:-1] += w * (p[:-2] + p[2:] - 2.0 * p[1:-1])
        if project is not None and (it % 3 == 2 or it == iters - 1):
            p[1:-1] = project(p[1:-1])
    return p


def decimate(path, min_step):
    """Drop samples closer together than min_step. Endpoints always survive."""
    path = np.asarray(path, dtype=float)
    if len(path) < 3:
        return path
    keep = [0]
    for i in range(1, len(path) - 1):
        if np.linalg.norm(path[i] - path[keep[-1]]) >= min_step:
            keep.append(i)
    keep.append(len(path) - 1)
    return path[keep]


def retrace_straight(graph, arc, surface, samples=None):
    """Re-lay an arc as a straight run across the surface between its nodes.

    A click-to-click segment has no shape of its own — it was *derived* from
    where its two endpoints were. When one of them moves, the honest answer is
    to lay the segment again between the new positions, not to deform the old
    samples: the whole arc should follow, which is what it looks like it should
    do.
    """
    a = np.asarray(graph.nodes[arc.a].co, dtype=float)
    b = np.asarray(graph.nodes[arc.b].co, dtype=float)
    n = samples if samples is not None else max(len(arc.path), 2)
    t = np.linspace(0.0, 1.0, n)[:, None]
    path = a * (1.0 - t) + b * t
    if surface is not None and n > 2:
        path[1:-1] = surface.project(path[1:-1])
    path[0], path[-1] = a, b
    arc.path = path
    if surface is not None:
        arc.pins = [surface.pin(p) for p in path]


def move_node(graph, nid, co, surface=None, falloff=1.0):
    """Move a node, taking every arc touching it along.

    How an arc follows depends on how it was made. A straight segment between
    two clicked points is re-laid end to end — its old samples described
    nothing but where the endpoints used to be. A freehand stroke *is* the
    artist's line, so it is bent with a smooth falloff instead of thrown away.

    Rewriting only the polyline's endpoint leaves every interior sample where
    it was, so the arc gets a spike at the node instead of curving — which is
    what "moving a node messes up the arc" looks like. The displacement is
    spread along the arc with a smooth falloff from the moved end, and the
    result is reprojected so it stays on the surface.

    ``falloff`` is the fraction of each arc's length that responds: 1.0 bends
    the whole arc, smaller values keep the far end pinned and bend only the
    part near the node.
    """
    co = np.asarray(co, dtype=float)
    node = graph.nodes[nid]
    delta = co - np.asarray(node.co, dtype=float)
    node.co = co
    if surface is not None:
        node.pin = surface.pin(co)
    if float(np.linalg.norm(delta)) <= 0.0:
        return

    for arc in graph.arcs.values():
        at_start = arc.a == nid
        at_end = arc.b == nid
        if not (at_start or at_end):
            continue

        if arc.rail == "straight":
            retrace_straight(graph, arc, surface)
            continue

        path = np.asarray(arc.path, dtype=float).copy()
        if len(path) < 2:
            continue

        seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        total = cum[-1]
        if total <= 1e-12:
            path[:] = co
        else:
            # distance from the moved end, normalised by the falloff reach
            d = cum if at_start else (total - cum)
            reach = max(total * max(min(falloff, 1.0), 1e-6), 1e-12)
            t = np.clip(d / reach, 0.0, 1.0)
            w = 1.0 - (t * t * (3.0 - 2.0 * t))       # smoothstep, 1 at the node
            if at_start and at_end:
                w = np.maximum(w, 1.0 - (np.clip((total - cum) / reach, 0, 1) ** 2
                                         * (3 - 2 * np.clip((total - cum) / reach, 0, 1))))
            path = path + delta * w[:, None]

        path[0] = graph.nodes[arc.a].co
        path[-1] = graph.nodes[arc.b].co
        if surface is not None and len(path) > 2:
            path[1:-1] = surface.project(path[1:-1])
            path[0] = graph.nodes[arc.a].co
            path[-1] = graph.nodes[arc.b].co
        arc.path = path
        if surface is not None:
            arc.pins = [surface.pin(pt) for pt in path]


def dissolve_node(graph, nid, surface=None):
    """Merge the two arcs at a valence-2 node back into one. -> new arc id."""
    touching = [(aid, arc) for aid, arc in graph.arcs.items()
                if arc.a == nid or arc.b == nid]
    if len(touching) != 2:
        return None
    (aid0, arc0), (aid1, arc1) = touching
    p0 = np.asarray(arc0.path, dtype=float)
    p1 = np.asarray(arc1.path, dtype=float)
    if arc0.b != nid:
        p0 = p0[::-1]
    far0 = arc0.a if arc0.b == nid else arc0.b
    if arc1.a != nid:
        p1 = p1[::-1]
    far1 = arc1.b if arc1.a == nid else arc1.a
    if far0 == far1:
        return None
    path = np.vstack([p0, p1[1:]])
    keep = [0] + [i for i in range(1, len(path))
                  if np.linalg.norm(path[i] - path[i - 1]) > 1e-12]
    path = path[keep]
    kind, rail = arc0.type, arc0.rail
    del graph.arcs[aid0]
    del graph.arcs[aid1]
    del graph.nodes[nid]
    return add_arc(graph, far0, far1, path, surface, kind, rail)
