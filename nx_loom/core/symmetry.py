"""Layout-level symmetry.

Mirroring the *graph* rather than the mesh is what makes the result exactly
symmetric instead of approximately so. Both halves share the nodes that sit on
the plane, so the seam is welded by construction — there is no mirror-weld
pass, no double vertices to merge, and no tolerance to tune.

Mirrored elements carry ``mirror_of`` and are **derived**: every sync throws
them all away and regenerates them. Nothing has to track them incrementally,
which is why deleting an authored arc removes its counterpart for free.
"""

from __future__ import annotations

import numpy as np

from .authoring import add_arc, new_node

AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}


def mirror_co(co, axis):
    out = np.array(co, dtype=float)
    out[AXIS_INDEX[axis]] *= -1.0
    return out


def signed(co, axis):
    return float(np.asarray(co, dtype=float)[AXIS_INDEX[axis]])


def sync(graph, axis, tol=1e-4, surface=None):
    """Regenerate the mirrored half of the layout. Returns a report."""
    if not axis or axis == "NONE":
        return _drop_derived(graph, surface)

    ax = AXIS_INDEX[axis]
    rep = _drop_derived(graph, surface)
    for arc in graph.arcs.values():
        arc.twin = None

    # Nodes near the plane are snapped onto it exactly. They are shared by both
    # halves rather than duplicated, which is what welds the seam.
    on_plane = set()
    for node in graph.nodes.values():
        if abs(node.co[ax]) <= tol:
            node.co[ax] = 0.0
            if surface is not None:
                node.pin = surface.pin(node.co)
            on_plane.add(node.id)
    for arc in graph.arcs.values():
        arc.path[0] = graph.nodes[arc.a].co
        arc.path[-1] = graph.nodes[arc.b].co
        pts = np.asarray(arc.path, dtype=float)
        near = np.abs(pts[:, ax]) <= tol
        if near.any():
            pts[near, ax] = 0.0
            arc.path = pts
    rep["on_plane"] = len(on_plane)

    # An arc drawn across the centre line is cut at the crossing, so each half
    # belongs to one side and the cut point becomes a shared on-plane node.
    rep["split"] = _split_crossing(graph, ax, tol, surface)

    off_plane = [a for a in graph.arcs.values()
                 if a.mirror_of is None and not _straddles_or_on(a, ax, tol)]

    # Pass 1: adopt counterparts the artist drew by hand. Without this a layout
    # that is already symmetric on both sides has nothing marked derived, so
    # the two halves are quantised independently and their counts drift apart —
    # which is exactly how a symmetric layout ends up with an asymmetric mesh.
    # The positive side is always the source, so the choice is deterministic.
    adopted = 0
    by_id = {a.id: a for a in off_plane}
    for arc in sorted(off_plane, key=lambda a: -_centroid(a, ax)):
        if arc.mirror_of is not None or _centroid(arc, ax) <= 0:
            continue
        m0, m1 = mirror_co(arc.path[0], axis), mirror_co(arc.path[-1], axis)
        for other in by_id.values():
            if other.id == arc.id or other.mirror_of is not None:
                continue
            if _centroid(other, ax) > 0:
                continue
            o0, o1 = np.asarray(other.path[0]), np.asarray(other.path[-1])
            if (np.linalg.norm(o0 - m0) <= tol and np.linalg.norm(o1 - m1) <= tol) or \
               (np.linalg.norm(o0 - m1) <= tol and np.linalg.norm(o1 - m0) <= tol):
                # `twin`, not `mirror_of`: this arc was drawn by hand. Marking
                # it derived would let the next sync delete and regenerate it,
                # churning arc ids and breaking every hole key and delta that
                # references them.
                other.twin = arc.id
                adopted += 1
                break

    # Pass 2: mirror whatever still has no counterpart, from either side.
    def _covered(m_path, skip_id):
        """Does this would-be mirror already lie along authored geometry?

        Twinning needs arc-to-arc correspondence, and two rings anchored on
        opposite sides of their limbs decompose into arcs rotated half a ring
        apart — no correspondence exists. The one outcome that must never
        happen is doubling geometry on top of what the artist drew, so a
        mirror whose path already hugs existing authored arcs is skipped
        outright (its counts stay untied, which the artist can pin).
        """
        probes = m_path[:: max(len(m_path) // 5, 1)]
        reach = max(tol, 0.3 * float(np.linalg.norm(
            np.diff(m_path, axis=0), axis=1).sum()))
        for probe in probes:
            best = None
            for other in off_plane:
                if other.id == skip_id:
                    continue
                path = np.asarray(other.path, dtype=float)
                a, ab = path[:-1], path[1:] - path[:-1]
                denom = np.einsum("ij,ij->i", ab, ab)
                denom[denom < 1e-20] = 1e-20
                t = np.clip(np.einsum("ij,ij->i", probe - a, ab) / denom, 0, 1)
                d = float(np.linalg.norm(a + ab * t[:, None] - probe, axis=1).min())
                best = d if best is None or d < best else best
            if best is None or best > reach:
                return False
        return True

    index = _position_index(graph)
    made = 0
    covered = 0
    for arc in [a for a in off_plane if a.mirror_of is None and a.twin is None]:
        m_path = np.array(arc.path, dtype=float)
        m_path[:, ax] *= -1.0
        if _find_arc(graph, m_path[0], m_path[-1], tol):
            continue
        if _covered(m_path, arc.id):
            covered += 1
            continue
        a = _node_at(graph, m_path[0], index, tol, surface, arc.a)
        b = _node_at(graph, m_path[-1], index, tol, surface, arc.b)
        if a == b:
            continue
        aid = add_arc(graph, a, b, m_path, surface, arc.type, arc.rail)
        graph.arcs[aid].mirror_of = arc.id
        made += 1

    rep["mirrored"] = made
    rep["adopted"] = adopted
    rep["covered"] = covered
    return rep


def _centroid(arc, ax):
    return float(np.asarray(arc.path, dtype=float)[:, ax].mean())


def _drop_derived(graph, surface=None):
    n_arcs = [aid for aid, arc in graph.arcs.items() if arc.mirror_of is not None]
    for aid in n_arcs:
        del graph.arcs[aid]
    used = {arc.a for arc in graph.arcs.values()} | {arc.b for arc in graph.arcs.values()}
    n_nodes = [nid for nid, node in graph.nodes.items()
               if node.mirror_of is not None and nid not in used]
    for nid in n_nodes:
        del graph.nodes[nid]
    # Only derived nodes are swept. A node with no arcs is authored data — the
    # point you just placed, before it has anything attached — and pruning
    # every orphan here deleted it on the very next refresh, which made
    # placing points impossible.
    return {"dropped_arcs": len(n_arcs), "dropped_nodes": len(n_nodes),
            "mirrored": 0, "adopted": 0, "split": 0, "on_plane": 0}


def _straddles_or_on(arc, ax, tol):
    v = np.asarray(arc.path, dtype=float)[:, ax]
    return bool(np.all(np.abs(v) <= tol))


def _position_index(graph):
    return {nid: np.asarray(node.co, dtype=float) for nid, node in graph.nodes.items()}


def _node_at(graph, co, index, tol, surface, source_id):
    co = np.asarray(co, dtype=float)
    for nid, pos in index.items():
        if float(np.linalg.norm(pos - co)) <= tol:
            return nid
    nid = new_node(graph, co, surface)
    graph.nodes[nid].mirror_of = source_id
    index[nid] = np.asarray(graph.nodes[nid].co, dtype=float)
    return nid


def _find_arc(graph, p0, p1, tol):
    for arc in graph.arcs.values():
        if arc.mirror_of is not None:
            continue
        a, b = np.asarray(arc.path[0]), np.asarray(arc.path[-1])
        if (np.linalg.norm(a - p0) <= tol and np.linalg.norm(b - p1) <= tol) or \
           (np.linalg.norm(a - p1) <= tol and np.linalg.norm(b - p0) <= tol):
            return True
    return False


def _split_crossing(graph, ax, tol, surface):
    from .authoring import split_arc
    count = 0
    for aid in list(graph.arcs):
        arc = graph.arcs.get(aid)
        if arc is None or arc.mirror_of is not None:
            continue
        v = np.asarray(arc.path, dtype=float)[:, ax]
        if v.min() >= -tol or v.max() <= tol:
            continue                        # stays on one side
        idx = None
        for i in range(len(v) - 1):
            if (v[i] > tol and v[i + 1] < -tol) or (v[i] < -tol and v[i + 1] > tol):
                idx = i
                break
        if idx is None:
            continue
        denom = v[idx] - v[idx + 1]
        t = 0.0 if abs(denom) < 1e-12 else float(v[idx] / denom)
        split_arc(graph, aid, idx, min(max(t, 0.0), 1.0), surface)
        count += 1
    return count


def representative(graph):
    """Map every arc to the arc whose subdivision count it must copy.

    Quantising over representatives instead of symmetrising afterwards is what
    guarantees the two halves get identical counts: a mirrored layout has a
    mirrored constraint system, so solving the reduced system solves both.
    """
    out = {}
    for aid, arc in graph.arcs.items():
        if arc.mirror_of in graph.arcs:
            out[aid] = arc.mirror_of
        elif arc.twin in graph.arcs:
            out[aid] = arc.twin
        else:
            out[aid] = aid
    return out


def symmetrize_verts(verts, axis, tol):
    """Force generated vertices into exact mirror pairs.

    The layout is mirrored exactly, but the generated positions are not: the
    reference mesh's own triangulation is asymmetric, so reprojecting onto it
    pulls the two halves apart by up to a triangle's width. Measured on a
    sphere that was ~2e-2 — invisible in isolation and very visible on a face.

    Positions are copied from the positive side to the negative one rather than
    averaged, so the result is deterministic and one half is authoritative.
    Vertices on the plane are pinned to it exactly.
    """
    verts = np.asarray(verts, dtype=float).copy()
    if not axis or axis == "NONE" or not len(verts):
        return verts, {"paired": 0, "seam": 0, "unpaired": 0}

    ax = AXIS_INDEX[axis]
    seam = np.abs(verts[:, ax]) <= tol
    verts[seam, ax] = 0.0

    pos = np.where(verts[:, ax] > tol)[0]
    neg = np.where(verts[:, ax] < -tol)[0]
    if not len(pos) or not len(neg):
        return verts, {"paired": 0, "seam": int(seam.sum()),
                       "unpaired": int(len(pos) + len(neg))}

    want = verts[pos].copy()
    want[:, ax] *= -1.0
    have = verts[neg]

    span = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0)))
    radius = max(span * 0.02, tol * 4.0)

    # One-to-one, closest pairs first. A plain per-vertex nearest search lets
    # two positive vertices claim the same partner, which leaves a third
    # unpaired and quietly asymmetric.
    d = np.linalg.norm(want[:, None, :] - have[None, :, :], axis=2)
    order = np.dstack(np.unravel_index(np.argsort(d, axis=None), d.shape))[0]
    used_p, used_n = set(), set()
    paired = 0
    for i, j in order:
        i, j = int(i), int(j)
        if d[i, j] > radius:
            break
        if i in used_p or j in used_n:
            continue
        verts[neg[j]] = want[i]
        used_p.add(i)
        used_n.add(j)
        paired += 1

    return verts, {"paired": paired, "seam": int(seam.sum()),
                   "unpaired": int(len(neg) - paired),
                   "halves_match": len(pos) == len(neg)}
