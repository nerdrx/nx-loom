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


def resolve_anchor(graph, point, radius, surface=None):
    """Where a click lands. -> ("node", nid) after creating/splitting as needed.

    Snapping to an existing arc splits it, which is what makes a T-junction
    something you can just draw into rather than having to plan for.
    """
    hit = nearest_node(graph, point, radius)
    if hit is not None:
        return hit[0], "node"
    hit = nearest_on_arc(graph, point, radius)
    if hit is not None:
        aid, index, t, _, _ = hit
        return split_arc(graph, aid, index, t, surface), "split"
    return new_node(graph, point, surface), "new"


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


def move_node(graph, nid, co, surface=None):
    """Move a node and drag the ends of every arc touching it."""
    co = np.asarray(co, dtype=float)
    node = graph.nodes[nid]
    node.co = co
    if surface is not None:
        node.pin = surface.pin(co)
    for arc in graph.arcs.values():
        if arc.a == nid:
            arc.path[0] = co
            if arc.pins:
                arc.pins[0] = node.pin
        if arc.b == nid:
            arc.path[-1] = co
            if arc.pins:
                arc.pins[-1] = node.pin


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
