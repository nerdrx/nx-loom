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


def _authored_items(graph):
    """Per-arc content tuples — the unit of incremental change tracking."""
    items = {}
    for aid in sorted(graph.arcs):
        arc = graph.arcs[aid]
        if arc.mirror_of is None:
            path = np.asarray(arc.path, dtype=float)
            items[aid] = [arc.a, arc.b, len(path),
                          round(float(path.sum()), 2),
                          round(float(np.abs(path).sum()), 2)]
    return items


def _authored_signature(graph, axis, tol):
    """Cheap content hash of everything sync's output depends on.

    Sync regenerates every mirrored arc from scratch — endpoint scans,
    coverage checks, a BVH re-pin per sample. Between two syncs where the
    authored half did not move, all of that reproduces what is already there,
    and rebuild paths call sync more than once per edit. O(n) hashing buys
    skipping the O(n^2) work.
    """
    # Rounding is deliberately coarse (1e-4 world units). Seam geometry
    # oscillates by pin round-trips (~1e-3 on the first cycles, ~1e-5 once
    # converged) between syncs, and a signature tighter than that noise never
    # matches, so the skip never fires. Any real gesture moves things by far
    # more than a tenth of a millimetre at avatar scale.
    h = []
    for nid in sorted(graph.nodes):
        node = graph.nodes[nid]
        if node.mirror_of is None:
            co = np.asarray(node.co, dtype=float)
            h.append((nid, round(co[0], 4), round(co[1], 4), round(co[2], 4)))
    for aid in sorted(graph.arcs):
        arc = graph.arcs[aid]
        if arc.mirror_of is None:
            path = np.asarray(arc.path, dtype=float)
            h.append((aid, arc.a, arc.b, len(path),
                      round(float(path.sum()), 2),
                      round(float(np.abs(path).sum()), 2)))
    return hash((axis, round(tol, 9), tuple(h)))


def sync(graph, axis, tol=1e-4, surface=None):
    """Regenerate the mirrored half of the layout. Returns a report."""
    if not axis or axis == "NONE":
        graph.settings.pop("sym_sig", None)
        return _drop_derived(graph, surface)

    # Snap BEFORE the signature. refresh_positions un-snaps near-plane
    # geometry by a pin round-trip (~1e-4) ahead of every call, so a signature
    # taken over the raw state never matches its stored value and the skip
    # never fires. Snapping first is idempotent and O(n), and it means both
    # the skip and the full path measure — and leave behind — the same
    # normalised state.
    ax = AXIS_INDEX[axis]
    on_plane = _snap_to_plane(graph, ax, tol, surface)
    sig = _authored_signature(graph, axis, tol)
    if graph.settings.get("sym_sig") == sig:
        derived = sum(1 for a in graph.arcs.values() if a.mirror_of is not None)
        return {"dropped_arcs": 0, "dropped_nodes": 0, "mirrored": derived,
                "adopted": 0, "split": 0, "on_plane": on_plane, "covered": 0,
                "skipped": True}

    # Per-arc incrementality: a mirror whose SOURCE arc is unchanged since the
    # last sync is already correct, and regenerating it — resampling, BVH
    # re-pinning every sample, endpoint matching — is the bulk of a full sync.
    # An edit touches one or two arcs; the other few hundred mirrors are kept.
    cur_items = _authored_items(graph)
    prev_items = {int(k): v for k, v in
                  (graph.settings.get("sym_arc_items") or {}).items()}
    unchanged = {aid for aid, item in cur_items.items()
                 if prev_items.get(aid) == item}
    kept = {arc.mirror_of for arc in graph.arcs.values()
            if arc.mirror_of is not None and arc.mirror_of in unchanged}

    rep = _drop_derived(graph, surface, keep_sources=kept)
    for arc in graph.arcs.values():
        arc.twin = None
    rep["on_plane"] = on_plane
    rep["kept"] = len(kept)

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
    # `twin`, not `mirror_of`: a hand-drawn counterpart must never become
    # derived, or the next sync deletes and regenerates the artist's own arc.
    # The tolerance is proportional to the arc — the seam tolerance is for the
    # seam, and real hand-drawn counterparts differ by centimetres — with a
    # midpoint check so roughly-shared endpoints are not enough by accident.
    # Vectorised over candidate arrays: the per-pair Python loop was O(n^2)
    # with ~80k centroid calls at avatar scale, half of every full sync.
    adopted = 0
    cent = {a.id: _centroid(a, ax) for a in off_plane}
    neg = [a for a in off_plane if cent[a.id] <= 0 and a.mirror_of is None]
    pos = sorted((a for a in off_plane
                  if cent[a.id] > 0 and a.mirror_of is None),
                 key=lambda a: a.id)
    if neg and pos:
        n0 = np.array([a.path[0] for a in neg], dtype=float)
        n1 = np.array([a.path[-1] for a in neg], dtype=float)
        nmid = np.array([_arc_mid(a.path) for a in neg], dtype=float)
        nlen = np.array([a.length() for a in neg], dtype=float)
        free = np.array([a.twin is None for a in neg], dtype=bool)
        for arc in pos:
            if not free.any():
                break
            m0 = mirror_co(arc.path[0], axis)
            m1 = mirror_co(arc.path[-1], axis)
            m_mid = mirror_co(_arc_mid(arc.path), axis)
            a_tol = np.maximum(tol, 0.25 * np.minimum(arc.length(), nlen))
            ok = free & (np.linalg.norm(nmid - m_mid, axis=1) <= a_tol * 2.0)
            fwd = (np.linalg.norm(n0 - m0, axis=1) <= a_tol) & \
                  (np.linalg.norm(n1 - m1, axis=1) <= a_tol)
            rev = (np.linalg.norm(n0 - m1, axis=1) <= a_tol) & \
                  (np.linalg.norm(n1 - m0, axis=1) <= a_tol)
            ok &= (fwd | rev)
            hits = np.nonzero(ok)[0]
            if len(hits):
                # Best match, not first match: with a tolerance proportional
                # to the arc, several candidates can qualify on a dense
                # layout, and letting index order decide steals an exact
                # counterpart's pairing for a merely-nearby arc.
                score = np.minimum(
                    np.linalg.norm(n0 - m0, axis=1)
                    + np.linalg.norm(n1 - m1, axis=1),
                    np.linalg.norm(n0 - m1, axis=1)
                    + np.linalg.norm(n1 - m0, axis=1))
                j = int(hits[np.argmin(score[hits])])
                neg[j].twin = arc.id
                free[j] = False
                adopted += 1

    # Pass 2: mirror whatever still has no counterpart, from either side.
    # Coverage: does a would-be mirror already lie along authored geometry?
    # (Two counterpart rings anchored on opposite sides have no arc-to-arc
    # correspondence, so twinning cannot apply — but doubling geometry on top
    # of the artist's must never happen either.) All authored path points go
    # into one soup, and each probe is a single vectorised distance query —
    # the per-arc segment loop here was quadratic in the layout.
    soup_by_owner = []
    soup_pts = []
    for other in off_plane:
        pts = np.asarray(other.path, dtype=float)
        soup_pts.append(pts)
        soup_by_owner.append(np.full(len(pts), other.id))
    soup = np.vstack(soup_pts) if soup_pts else np.zeros((0, 3))
    soup_owner = np.concatenate(soup_by_owner) if soup_by_owner else np.zeros(0)

    # A KD-tree over the soup, when Blender's mathutils is around. Hundreds of
    # arcs re-verify their coverage on every full sync (their verdicts cannot
    # be cached — a stale "covered" would hide a deleted counterpart), and a
    # brute-force scan per probe was the remaining bulk of an edit's sync.
    kd = None
    try:
        from mathutils.kdtree import KDTree
        kd = KDTree(len(soup))
        for idx, pnt in enumerate(soup):
            kd.insert(pnt, idx)
        kd.balance()
    except ImportError:
        pass

    def _covered(m_path, skip_id):
        if not len(soup):
            return False
        probes = m_path[:: max(len(m_path) // 5, 1)]
        reach = max(tol, 0.3 * float(np.linalg.norm(
            np.diff(m_path, axis=0), axis=1).sum()))
        if kd is not None:
            for probe in probes:
                ok = False
                for _co, idx, dist in kd.find_n(probe, 4):
                    if dist <= reach and soup_owner[idx] != skip_id:
                        ok = True
                        break
                if not ok:
                    return False
            return True
        keep = soup_owner != skip_id
        pts = soup[keep]
        if not len(pts):
            return False
        for probe in probes:
            if float(np.linalg.norm(pts - probe, axis=1).min()) > reach:
                return False
        return True

    already_mirrored = {arc.mirror_of for arc in graph.arcs.values()
                        if arc.mirror_of is not None}
    index = _position_index(graph)
    made = 0
    covered = 0
    for arc in [a for a in off_plane if a.mirror_of is None and a.twin is None
                and a.id not in already_mirrored]:
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
    graph.settings["sym_sig"] = _authored_signature(graph, axis, tol)
    graph.settings["sym_arc_items"] = {str(k): v for k, v in
                                       _authored_items(graph).items()}
    return rep


def _arc_mid(path):
    """The true arc-length midpoint of a polyline.

    ``path[len(path) // 2]`` is not it: on a two-sample straight arc that is
    the endpoint, so a reversed-orientation counterpart 'mismatches' by the
    whole arc length and adoption refuses an exact mirror pair.
    """
    p = np.asarray(path, dtype=float)
    if len(p) < 3:
        return p.mean(axis=0)
    seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    half = cum[-1] * 0.5
    i = int(np.searchsorted(cum, half, side="right") - 1)
    i = min(max(i, 0), len(seg) - 1)
    t = 0.0 if seg[i] <= 0 else (half - cum[i]) / seg[i]
    return p[i] + (p[i + 1] - p[i]) * t


def _centroid(arc, ax):
    return float(np.asarray(arc.path, dtype=float)[:, ax].mean())


def _snap_to_plane(graph, ax, tol, surface=None):
    """Zero near-plane geometry exactly onto the plane. Idempotent, O(n)."""
    on_plane = 0
    for node in graph.nodes.values():
        if abs(node.co[ax]) <= tol:
            node.co[ax] = 0.0
            if surface is not None:
                node.pin = surface.pin(node.co)
            on_plane += 1
    for arc in graph.arcs.values():
        arc.path[0] = graph.nodes[arc.a].co
        arc.path[-1] = graph.nodes[arc.b].co
        pts = np.asarray(arc.path, dtype=float)
        near = np.abs(pts[:, ax]) <= tol
        if near.any():
            pts[near, ax] = 0.0
            arc.path = pts
    return on_plane


def _drop_derived(graph, surface=None, keep_sources=()):
    n_arcs = [aid for aid, arc in graph.arcs.items()
              if arc.mirror_of is not None and arc.mirror_of not in keep_sources]
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
    ids = list(graph.nodes)
    pos = np.array([graph.nodes[n].co for n in ids], dtype=float) \
        if ids else np.zeros((0, 3))
    return {"ids": ids, "pos": pos}


def _node_at(graph, co, index, tol, surface, source_id):
    co = np.asarray(co, dtype=float)
    if len(index["ids"]):
        d = np.linalg.norm(index["pos"] - co, axis=1)
        j = int(np.argmin(d))
        if float(d[j]) <= tol:
            return index["ids"][j]
    nid = new_node(graph, co, surface)
    graph.nodes[nid].mirror_of = source_id
    index["ids"].append(nid)
    index["pos"] = np.vstack([index["pos"],
                              np.asarray(graph.nodes[nid].co, dtype=float)[None]])
    return nid


def _find_arc(graph, p0, p1, tol):
    authored = [a for a in graph.arcs.values() if a.mirror_of is None]
    if not authored:
        return False
    e0 = np.array([a.path[0] for a in authored], dtype=float)
    e1 = np.array([a.path[-1] for a in authored], dtype=float)
    fwd = (np.linalg.norm(e0 - p0, axis=1) <= tol) & \
          (np.linalg.norm(e1 - p1, axis=1) <= tol)
    rev = (np.linalg.norm(e0 - p1, axis=1) <= tol) & \
          (np.linalg.norm(e1 - p0, axis=1) <= tol)
    return bool((fwd | rev).any())


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


def unpaired_arcs(graph, axis, tol=1e-4):
    """Authored off-plane arcs with no symmetry partner at all.

    These are the arcs that quietly break "working mirrored": both sides carry
    hand-drawn geometry too different to pair, so the duplication guard leaves
    them alone and the two sides quantise INDEPENDENTLY — which is how one
    side of a mirrored-looking layout can fail to solve while the other side
    is fine. A paired region cannot do that: mirrored patches share their
    counts through representative arcs and solve or fail together.
    """
    if not axis or axis == "NONE":
        return []
    ax = AXIS_INDEX[axis]
    mirrored_from = {a.mirror_of for a in graph.arcs.values()
                     if a.mirror_of is not None}
    twinned_to = {a.twin for a in graph.arcs.values() if a.twin is not None}
    out = []
    for aid, arc in graph.arcs.items():
        if arc.mirror_of is not None or arc.twin is not None:
            continue
        if aid in mirrored_from or aid in twinned_to:
            continue
        if _straddles_or_on(arc, ax, tol):
            continue
        out.append(aid)
    return out


def enforce_mirrored_patches(graph, axis, tol=1e-4):
    """Replace discovered mirrored-side patches with mirror images of the
    authored side's decomposition.

    Discovery re-derives patches per side from surface normals and a corner
    angle threshold, and the reference sculpt's triangulation is not
    symmetric — so a borderline corner call can flip on one side only, giving
    two exactly-mirrored regions DIFFERENT patch structures and therefore
    different constraints. That is how one cheek of a fully mirrored layout
    fails to solve while the other is fine. A derived region's decomposition
    is not something to rediscover: it is the authored side's, mirrored.

    Only patches whose off-plane arcs are all derived are replaced; regions
    the artist drew on both sides (twins) keep their own discovery.
    """
    if not axis or axis == "NONE" or not graph.patches:
        return 0
    arc_map = {}
    for aid, arc in graph.arcs.items():
        if arc.mirror_of is not None:
            arc_map[arc.mirror_of] = aid
    if not arc_map:
        return 0
    node_map = {}
    for nid, node in graph.nodes.items():
        if node.mirror_of is not None:
            node_map[node.mirror_of] = nid
    ax = AXIS_INDEX[axis]

    def on_plane_arc(aid):
        return _straddles_or_on(graph.arcs[aid], ax, tol)

    def on_plane_node(nid):
        return abs(float(graph.nodes[nid].co[ax])) <= tol

    def map_arc(aid):
        if aid in arc_map:
            return arc_map[aid]
        return aid if on_plane_arc(aid) else None

    def map_node(nid):
        if nid in node_map:
            return node_map[nid]
        return nid if on_plane_node(nid) else None

    derived_arc_ids = set(arc_map.values())

    authored_patches = []
    doomed = []
    for pid, patch in graph.patches.items():
        arcs = {a for side in patch.arc_sides() for a in side}
        off = [a for a in arcs if not on_plane_arc(a)]
        if off and all(a in derived_arc_ids for a in off):
            doomed.append(pid)
        elif off and all(graph.arcs[a].mirror_of is None
                         and graph.arcs[a].twin is None for a in off) \
                and all(map_arc(a) is not None for a in arcs):
            authored_patches.append(pid)

    if not authored_patches:
        return 0
    for pid in doomed:
        del graph.patches[pid]

    from .graph import Patch
    next_id = (max(graph.patches) + 1) if graph.patches else 0
    made = 0
    for pid in authored_patches:
        src = graph.patches[pid]
        n = len(src.sides)
        corners = [map_node(src.corners[(n - j) % n]) for j in range(n)]
        if any(c is None for c in corners):
            continue
        # mirroring flips orientation: walk the boundary the other way round —
        # sides in reverse order, arcs within each side reversed, and every
        # direction flag flipped
        sides = []
        ok = True
        for j in range(n):
            src_side = src.sides[(n - 1 - j) % n]
            side = []
            for aid, rev in reversed(src_side):
                m = map_arc(aid)
                if m is None:
                    ok = False
                    break
                side.append((m, not rev if aid in arc_map else not rev))
            if not ok:
                break
            sides.append(side)
        if not ok:
            continue
        mirrored = Patch(next_id, sides, corners, fill=src.fill)
        graph.patches[next_id] = mirrored
        next_id += 1
        made += 1
    return made


def mismatched_twins(graph, axis, rel_tol=0.05):
    """Twinned pairs whose shapes are not actually mirror images.

    Twins tie subdivision counts, nothing more — both sides keep their own
    hand-drawn geometry. When the shapes drift apart the layout LOOKS mirrored
    and counts ARE mirrored, yet the two sides are different surfaces, which
    is invisible in the solve and very visible in the mesh. Returns
    [(arc_id, twin_id, deviation), ...] where deviation is the mean distance
    between one path and the mirror of its partner, relative to arc length.
    """
    if not axis or axis == "NONE":
        return []
    ax = AXIS_INDEX[axis]
    out = []
    for aid, arc in graph.arcs.items():
        if arc.twin is None or arc.twin not in graph.arcs:
            continue
        other = graph.arcs[arc.twin]
        m = np.asarray(other.path, dtype=float).copy()
        m[:, ax] *= -1.0
        p = np.asarray(arc.path, dtype=float)
        k = min(len(p), len(m))
        idx_p = np.linspace(0, len(p) - 1, k).astype(int)
        idx_m = np.linspace(0, len(m) - 1, k).astype(int)
        d_fwd = np.linalg.norm(p[idx_p] - m[idx_m], axis=1).mean()
        d_rev = np.linalg.norm(p[idx_p] - m[idx_m][::-1], axis=1).mean()
        dev = float(min(d_fwd, d_rev))
        ln = max(arc.length(), 1e-9)
        if dev / ln > rel_tol:
            out.append((aid, arc.twin, dev))
    return out


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
    # unpaired and quietly asymmetric. Candidates come from a spatial grid:
    # the full pairwise matrix was O(V^2) and became the dominant rebuild cost
    # as meshes grew (a 5k-vertex half is 25M distances per rebuild).
    cell = radius
    grid = {}
    for j, pnt in enumerate(have):
        key = tuple(np.floor(pnt / cell).astype(int))
        grid.setdefault(key, []).append(j)

    cand = []
    offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
               for dz in (-1, 0, 1)]
    for i, target in enumerate(want):
        base = tuple(np.floor(target / cell).astype(int))
        for off in offsets:
            for j in grid.get((base[0] + off[0], base[1] + off[1],
                               base[2] + off[2]), ()):
                dist = float(np.linalg.norm(have[j] - target))
                if dist <= radius:
                    cand.append((dist, i, j))
    cand.sort()
    used_p, used_n = set(), set()
    paired = 0
    for dist, i, j in cand:
        if i in used_p or j in used_n:
            continue
        verts[neg[j]] = want[i]
        used_p.add(i)
        used_n.add(j)
        paired += 1

    return verts, {"paired": paired, "seam": int(seam.sum()),
                   "unpaired": int(len(neg) - paired),
                   "halves_match": len(pos) == len(neg)}
