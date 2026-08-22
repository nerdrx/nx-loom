"""The layout graph — the actual document (SPEC §1).

Nodes, arcs and the patches derived from them. Patch discovery is planar-graph
face traversal using a rotational order of arcs around each node, taken in the
node's tangent plane. Serialisation is plain JSON so the whole document is
diffable and survives a Blender version bump.
"""

from __future__ import annotations

import json

import numpy as np

SCHEMA_VERSION = 1
GRAPH_KEY = "nx_loom_graph"


class Node:
    __slots__ = ("id", "pin", "co", "kind")

    def __init__(self, id, co, pin=None, kind="corner"):
        self.id = int(id)
        self.co = np.asarray(co, dtype=float)
        self.pin = pin
        self.kind = kind

    def to_dict(self):
        return {"id": self.id, "co": [float(x) for x in self.co],
                "pin": list(self.pin) if self.pin else None, "kind": self.kind}

    @staticmethod
    def from_dict(d):
        return Node(d["id"], d["co"], tuple(d["pin"]) if d.get("pin") else None,
                    d.get("kind", "corner"))


class Arc:
    __slots__ = ("id", "a", "b", "path", "pins", "type", "rail", "n", "n_lock")

    def __init__(self, id, a, b, path, type="flow", rail="surface",
                 pins=None, n=None, n_lock=None):
        self.id = int(id)
        self.a = int(a)
        self.b = int(b)
        self.path = np.asarray(path, dtype=float)
        self.pins = pins
        self.type = type
        self.rail = rail
        self.n = n
        self.n_lock = n_lock

    def length(self):
        if len(self.path) < 2:
            return 0.0
        return float(np.linalg.norm(np.diff(self.path, axis=0), axis=1).sum())

    def to_dict(self):
        return {"id": self.id, "a": self.a, "b": self.b,
                "path": [[float(x) for x in p] for p in self.path],
                "pins": self.pins, "type": self.type, "rail": self.rail,
                "n": self.n, "n_lock": self.n_lock}

    @staticmethod
    def from_dict(d):
        return Arc(d["id"], d["a"], d["b"], d["path"], d.get("type", "flow"),
                   d.get("rail", "surface"), d.get("pins"), d.get("n"), d.get("n_lock"))


class Patch:
    __slots__ = ("id", "sides", "corners", "fill")

    def __init__(self, id, sides, corners, fill="coons"):
        self.id = int(id)
        self.sides = sides          # list of sides; side = list of (arc_id, reversed)
        self.corners = corners      # node id per side, side i runs corners[i] -> corners[i+1]
        self.fill = fill

    def arc_sides(self):
        return [[arc for arc, _ in side] for side in self.sides]

    def to_dict(self):
        return {"id": self.id,
                "sides": [[[int(a), bool(r)] for a, r in s] for s in self.sides],
                "corners": [int(c) for c in self.corners], "fill": self.fill}

    @staticmethod
    def from_dict(d):
        return Patch(d["id"], [[(int(a), bool(r)) for a, r in s] for s in d["sides"]],
                     d["corners"], d.get("fill", "coons"))


class LayoutGraph:
    def __init__(self, reference=""):
        self.version = SCHEMA_VERSION
        self.reference = reference
        self.nodes = {}
        self.arcs = {}
        self.patches = {}
        self.settings = {"target_edge": 0.1}

    # -- serialisation ---------------------------------------------------

    def to_json(self):
        return json.dumps({
            "version": self.version, "reference": self.reference,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "arcs": [a.to_dict() for a in self.arcs.values()],
            "patches": [p.to_dict() for p in self.patches.values()],
            "settings": self.settings,
        })

    @staticmethod
    def from_json(text):
        d = json.loads(text)
        if d.get("version", 1) > SCHEMA_VERSION:
            raise ValueError(f"layout schema v{d['version']} is newer than this addon")
        g = LayoutGraph(d.get("reference", ""))
        g.nodes = {n["id"]: Node.from_dict(n) for n in d.get("nodes", [])}
        g.arcs = {a["id"]: Arc.from_dict(a) for a in d.get("arcs", [])}
        g.patches = {p["id"]: Patch.from_dict(p) for p in d.get("patches", [])}
        g.settings = d.get("settings", {"target_edge": 0.1})
        return g

    # -- topology --------------------------------------------------------

    def incident(self):
        out = {nid: [] for nid in self.nodes}
        for arc in self.arcs.values():
            out.setdefault(arc.a, []).append((arc.id, False))
            out.setdefault(arc.b, []).append((arc.id, True))
        return out

    def valence(self):
        return {nid: len(v) for nid, v in self.incident().items()}

    def refresh_positions(self, surface):
        """Re-derive world positions from pins after the reference changed."""
        for n in self.nodes.values():
            if n.pin is not None:
                n.co = np.asarray(surface.unpin(n.pin), dtype=float)
        for a in self.arcs.values():
            if a.pins:
                pts = [surface.unpin(p) if p else a.path[i]
                       for i, p in enumerate(a.pins)]
                a.path = np.asarray(pts, dtype=float)
            a.path[0] = self.nodes[a.a].co
            a.path[-1] = self.nodes[a.b].co

    # -- patch discovery -------------------------------------------------

    def discover_patches(self, normal_at=None, corner_angle=50.0):
        """Find patches by planar-graph face traversal. Returns a report.

        A node is a *corner* of a patch when its valence is not 2 (a junction)
        **or** the traversal turns by more than ``corner_angle`` degrees there.
        The valence rule alone is not enough: the corner of a plain grid is a
        degree-2 vertex with a 90 degree turn, and treating it as mid-side
        would hand the filler a 3-sided patch where a quad belongs."""
        inc = self.incident()
        order = {nid: self._ccw_order(nid, inc[nid], normal_at) for nid in self.nodes}
        pos_in_order = {
            nid: {half: i for i, half in enumerate(order[nid])} for nid in self.nodes
        }

        def other(arc_id, nid):
            arc = self.arcs[arc_id]
            return arc.b if arc.a == nid else arc.a

        def next_half(arc_id, frm):
            """Walk into the face on the left of frm -> to."""
            to = other(arc_id, frm)
            rev = (arc_id, self.arcs[arc_id].a == frm)
            ring = order[to]
            i = pos_in_order[to].get(rev)
            if i is None:
                i = next((k for k, (aid, _) in enumerate(ring) if aid == arc_id), 0)
            nxt = ring[(i - 1) % len(ring)]
            return nxt[0], to

        halves = set()
        for arc in self.arcs.values():
            halves.add((arc.id, arc.a))
            halves.add((arc.id, arc.b))

        cycles = []
        while halves:
            start = next(iter(halves))
            cycle = []
            cur = start
            for _ in range(len(halves) + 4):
                if cur not in halves:
                    break
                halves.discard(cur)
                cycle.append(cur)
                cur = next_half(*cur)
                if cur == start:
                    break
            if len(cycle) >= 2:
                cycles.append(cycle)

        val = self.valence()
        self.patches = {}
        rejected = {"outer": 0, "no_corners": 0, "bigon": 0}
        pid = 0
        for cycle in cycles:
            if not self._is_interior(cycle, normal_at):
                rejected["outer"] += 1
                continue
            cos_lim = np.cos(np.radians(corner_angle))
            corner_at = [
                i for i, (aid, frm) in enumerate(cycle)
                if val.get(frm, 0) != 2
                or self._turn_cos(cycle, i) < cos_lim
            ]
            if not corner_at:
                rejected["no_corners"] += 1
                continue
            sides, corners = [], []
            for k, start_i in enumerate(corner_at):
                stop_i = corner_at[(k + 1) % len(corner_at)]
                side = []
                i = start_i
                while True:
                    aid, frm = cycle[i]
                    side.append((aid, self.arcs[aid].b == frm))
                    i = (i + 1) % len(cycle)
                    if i == stop_i:
                        break
                sides.append(side)
                corners.append(cycle[start_i][1])
            if len(sides) < 3:
                rejected["bigon"] += 1
                continue
            self.patches[pid] = Patch(pid, sides, corners)
            pid += 1

        return {"patches": len(self.patches), "cycles": len(cycles), "rejected": rejected}

    def _turn_cos(self, cycle, i):
        """cos of the angle between arriving and leaving directions at cycle[i]."""
        aid_in, _ = cycle[(i - 1) % len(cycle)]
        aid_out, frm = cycle[i]
        arc_in, arc_out = self.arcs[aid_in], self.arcs[aid_out]
        p_in = arc_in.path if arc_in.b == frm else arc_in.path[::-1]
        p_out = arc_out.path if arc_out.a == frm else arc_out.path[::-1]
        if len(p_in) < 2 or len(p_out) < 2:
            return 1.0
        d_in = p_in[-1] - p_in[-2]
        d_out = p_out[1] - p_out[0]
        na, nb = np.linalg.norm(d_in), np.linalg.norm(d_out)
        if na < 1e-12 or nb < 1e-12:
            return 1.0
        return float(np.clip((d_in / na) @ (d_out / nb), -1.0, 1.0))

    def _ccw_order(self, nid, incident, normal_at):
        """Sort arcs around a node by angle in its tangent plane.

        The plane comes from a PCA of the *incident arc directions*, not from
        the reference normal. On a sharp rim the BVH normal is whichever facet
        the nearest-point query happened to land on — cap or wall — and a node
        that picks the wrong one gets a scrambled rotation system, which sends
        the face traversal through the wrong arc and fuses patches that should
        be separate. The arc directions are unambiguous there; the surface
        normal is only used to fix the plane's sign so orientation stays
        consistent with the reference.
        """
        p = self.nodes[nid].co
        dirs = []
        for aid, reversed_ in incident:
            arc = self.arcs[aid]
            path = arc.path[::-1] if reversed_ else arc.path
            d = path[1] - path[0] if len(path) > 1 else np.array([1.0, 0.0, 0.0])
            nd = np.linalg.norm(d)
            dirs.append(d / nd if nd > 1e-12 else np.array([1.0, 0.0, 0.0]))
        D = np.asarray(dirs)

        if len(D) >= 2:
            # smallest principal direction of the arc star = the plane normal
            _, _, vt = np.linalg.svd(D - D.mean(axis=0) if len(D) > 2 else D)
            nrm = vt[-1]
        else:
            nrm = np.array([0.0, 0.0, 1.0])
        if np.linalg.norm(nrm) < 1e-12:
            nrm = np.array([0.0, 0.0, 1.0])
        nrm = nrm / np.linalg.norm(nrm)

        ref = np.asarray(normal_at(p), dtype=float) if normal_at else None
        if ref is not None and np.linalg.norm(ref) > 1e-12 and (nrm @ ref) < 0:
            nrm = -nrm

        t = D[0] - nrm * (D[0] @ nrm)
        if np.linalg.norm(t) < 1e-9:
            t = np.array([1.0, 0.0, 0.0])
            t = t - nrm * (t @ nrm)
            if np.linalg.norm(t) < 1e-9:
                t = np.array([0.0, 1.0, 0.0]) - nrm * (np.array([0.0, 1.0, 0.0]) @ nrm)
        t /= max(np.linalg.norm(t), 1e-12)
        b = np.cross(nrm, t)

        def angle(item):
            i = incident.index(item)
            d = D[i] - nrm * (D[i] @ nrm)
            if np.linalg.norm(d) < 1e-12:
                return 0.0
            return float(np.arctan2(d @ b, d @ t))

        return sorted(incident, key=angle)

    def _is_interior(self, cycle, normal_at):
        """Vector area of the cycle must agree with the surface normal."""
        pts = []
        for aid, frm in cycle:
            arc = self.arcs[aid]
            path = arc.path if arc.a == frm else arc.path[::-1]
            pts.extend(path[:-1])
        if len(pts) < 3:
            return False
        pts = np.asarray(pts)
        c = pts.mean(axis=0)
        area = np.cross(pts - c, np.roll(pts, -1, axis=0) - c).sum(axis=0) * 0.5
        if np.linalg.norm(area) < 1e-14:
            return False
        nrm = np.asarray(normal_at(c)) if normal_at else np.array([0.0, 0.0, 1.0])
        if np.linalg.norm(nrm) < 1e-12:
            return True
        return float(area @ nrm) > 0.0


# -- authoring bootstrap ----------------------------------------------------

def from_edge_chains(points, chains, reference=""):
    """Build a graph from vertex positions plus chains of vertex indices.

    ``chains`` is the output of :func:`trace_chains`. Chain endpoints become
    nodes; interior chain vertices become arc path samples.
    """
    g = LayoutGraph(reference)
    pts = np.asarray(points, dtype=float)
    node_of = {}

    def node(vi):
        if vi not in node_of:
            nid = len(g.nodes)
            g.nodes[nid] = Node(nid, pts[vi])
            node_of[vi] = nid
        return node_of[vi]

    for chain in chains:
        a, b = node(chain[0]), node(chain[-1])
        aid = len(g.arcs)
        g.arcs[aid] = Arc(aid, a, b, pts[list(chain)])
    return g


def trace_chains(edges, points=None, corner_angle=50.0):
    """Split a set of edges into chains between junction/end vertices.

    A vertex of degree 2 is *not* a corner — it just subdivides a side — unless
    the chain turns sharply there, which is a corner in every sense that
    matters. Pure cycles with no junction get one artificial break so they
    still form arcs.
    """
    adj = {}
    for u, v in edges:
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)
    breaks = {v for v, nb in adj.items() if len(nb) != 2}

    if points is not None:
        pts = np.asarray(points, dtype=float)
        cos_lim = np.cos(np.radians(corner_angle))
        for v, nb in adj.items():
            if len(nb) != 2:
                continue
            a, b = sorted(nb)
            d1, d2 = pts[v] - pts[a], pts[b] - pts[v]
            n1, n2 = np.linalg.norm(d1), np.linalg.norm(d2)
            if n1 < 1e-12 or n2 < 1e-12:
                continue
            if float((d1 / n1) @ (d2 / n2)) < cos_lim:
                breaks.add(v)

    seen = set()
    chains = []

    def walk(start, first):
        chain = [start, first]
        prev, cur = start, first
        while cur not in breaks:
            nxt = [w for w in adj[cur] if w != prev]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            chain.append(cur)
            if cur == start:
                break
        return chain

    for v in sorted(breaks):
        for w in sorted(adj[v]):
            if (v, w) in seen:
                continue
            chain = walk(v, w)
            for i in range(len(chain) - 1):
                seen.add((chain[i], chain[i + 1]))
                seen.add((chain[i + 1], chain[i]))
            chains.append(chain)

    # leftover pure cycles
    for u, v in edges:
        if (u, v) in seen:
            continue
        chain = walk(u, v)
        if chain[-1] != u:
            chain.append(u)
        for i in range(len(chain) - 1):
            seen.add((chain[i], chain[i + 1]))
            seen.add((chain[i + 1], chain[i]))
        mid = len(chain) // 2
        chains.append(chain[:mid + 1])
        chains.append(chain[mid:])
    return chains
