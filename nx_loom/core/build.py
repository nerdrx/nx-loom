"""Graph -> mesh. The rebuild pipeline of SPEC §4.

Arc vertices are instantiated exactly once and both neighbouring patches index
into them, so patches are welded by construction — there is no distance merge
anywhere in this file, and there must never be one.
"""

from __future__ import annotations

import numpy as np

from .fill import fill_patch
from .quantize import quantize, solve_splits
from .surface import resample
from .symmetry import representative


BACKGROUND_MIN_RATIO = 6.0
BACKGROUND_MIN_SHARE = 0.5


def background_patches(graph):
    """Patches that are 'the rest of the model' rather than a region you drew.

    A closed loop drawn around a limb splits a closed surface into two valid
    regions: the limb, and everything else. Both are real patches, so filling
    them all sprays geometry over the entire hull. A region that dwarfs every
    other one was almost certainly not the thing being worked on, so it is left
    alone and reported instead of filled.
    """
    if len(graph.patches) < 2:
        return set()
    areas = {pid: graph.patch_area(pid) for pid in graph.patches}
    values = [a for a in areas.values() if a > 0]
    if not values:
        return set()
    smallest, total = min(values), sum(values)
    if smallest <= 0 or total <= 0:
        return set()
    return {pid for pid, a in areas.items()
            if a > smallest * BACKGROUND_MIN_RATIO and a > total * BACKGROUND_MIN_SHARE}


def _solve_counts(graph, target_edge, fill_background=False):
    """Quantise only. Shared by build() and the quad-count estimator.

    Memoised on everything the solve reads. The dense KKT solve is the floor
    of a warm rebuild once filling is cached, and it re-runs identically for
    every rebuild where no arc, lock or density changed.
    """
    rep_of = representative(graph)
    rep_ids = sorted(set(rep_of.values()))
    lengths = {r: graph.arcs[r].length() for r in rep_ids}

    # Locks are collected from EVERY arc and mapped onto its representative.
    # Reading them off the representatives alone silently dropped any pin on a
    # mirrored or twinned arc — with symmetry on that is half of them, so
    # pinning those arcs appeared to do nothing at all.
    locks, lock_conflicts = {}, []
    for aid, arc in graph.arcs.items():
        if not arc.n_lock:
            continue
        r = rep_of[aid]
        want = max(1, int(arc.n_lock))
        if r in locks and locks[r] != want:
            lock_conflicts.append((aid, r, locks[r], want))
            continue
        locks[r] = want

    def rep_sides(pid):
        return [[rep_of[a] for a in side]
                for side in graph.patches[pid].arc_sides()]

    # A patch asking for more resolution is expressed as a longer arc: the
    # quantiser already balances inconsistent targets by least squares, so this
    # needs no special case in the solver at all.
    # Density is folded across every arc onto its representative, exactly as
    # locks are — reading it off the representatives alone drops any override
    # whose patch sits on the mirrored side.
    dens = graph.arc_density()
    if dens:
        by_rep = {}
        for aid, mult in dens.items():
            by_rep.setdefault(rep_of.get(aid, aid), []).append(mult)
        lengths = {r: lengths[r] * (sum(by_rep[r]) / len(by_rep[r]))
                   if r in by_rep else lengths[r] for r in rep_ids}

    key = (round(target_edge, 9), fill_background,
           tuple(sorted((r, round(lengths[r], 4)) for r in rep_ids)),
           tuple(sorted(locks.items())),
           tuple(tuple(map(tuple, rep_sides(p))) for p in sorted(graph.patches)))
    hit = _COUNT_CACHE.get(hash(key))
    if hit is not None:
        counts_rep, qrep = hit
    else:
        # The last successful counts ride on the arcs themselves. An edit that
        # changes lengths but not topology cannot make the system infeasible,
        # so they are a guaranteed-valid fallback when the fresh solve stalls.
        seed = {r: graph.arcs[r].n for r in rep_ids
                if graph.arcs[r].n is not None}
        counts_rep, qrep = quantize(rep_ids, lengths, target_edge,
                                    list(graph.patches), rep_sides, locks,
                                    seed=seed or None)
        if not qrep["unsatisfied_patches"]:
            # never cache a failure: a later call may carry a better seed
            if len(_COUNT_CACHE) >= _COUNT_CACHE_CAP:
                _COUNT_CACHE.clear()
            _COUNT_CACHE[hash(key)] = (counts_rep, qrep)
    qrep = dict(qrep)
    qrep["lock_conflicts"] = lock_conflicts
    return {aid: counts_rep[rep_of[aid]] for aid in graph.arcs}, qrep


def estimate_quads(graph, target_edge, fill_background=False):
    """Predicted face count, without filling anything.

    A quad patch of p x q contributes p*q; an n-sided patch split at a[i]
    contributes sum(a[i] * a[i+1]). Both come straight out of the quantiser, so
    a face budget can be solved for without ever building geometry.
    """
    if not graph.patches:
        return 0
    counts, qrep = _solve_counts(graph, target_edge, fill_background)
    skip = set(qrep["unsatisfied_patches"])
    if not fill_background:
        skip |= background_patches(graph)

    total = 0
    for pid, patch in graph.patches.items():
        if pid in skip or patch.fill == "hole":
            continue
        sides = [sum(counts[a] for a in side) for side in patch.arc_sides()]
        if len(sides) == 4:
            total += sides[0] * sides[1]
        elif len(sides) >= 3:
            a = solve_splits(sides)
            if a is None:
                continue
            total += int(sum(a[i] * a[(i + 1) % len(a)] for i in range(len(a))))
    return total


def floor_faces(graph, fill_background=False):
    """Fewest faces this layout can express.

    A layout of N patches has a hard minimum: every side of a non-quad patch
    needs at least two segments, so the structure itself costs faces. Asking
    for fewer is not a solver failure, it is a request the layout cannot
    represent — the answer is a coarser *layout*, not a coarser solve.
    """
    if not graph.patches:
        return 0
    span = 0.0
    for pid in graph.patches:
        pts = graph.patch_boundary(pid)
        if len(pts):
            span = max(span, float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0))))
    return estimate_quads(graph, max(span * 4.0, 1.0), fill_background)


def solve_edge_for_count(graph, target_count, fill_background=False,
                         tol=0.02, max_iter=40):
    """Target edge length that yields ~target_count faces.

    Bisected in log space on a monotone-ish curve. The count is a step
    function of the edge length — it can only change in whole subdivisions —
    so an exact hit is often impossible; the closest achievable count is
    returned along with the edge that produced it.
    """
    if not graph.patches or target_count < 1:
        return graph.settings.get("target_edge", 0.1), 0

    area = sum(graph.patch_area(pid) for pid in graph.patches)
    guess = max((area / max(target_count, 1)) ** 0.5, 1e-6)

    lo, hi = guess, guess
    n_lo = n_hi = estimate_quads(graph, guess, fill_background)
    for _ in range(24):
        if n_lo >= target_count:
            break
        lo *= 0.7
        n_lo = estimate_quads(graph, lo, fill_background)
    for _ in range(24):
        if n_hi <= target_count:
            break
        hi *= 1.4
        n_hi = estimate_quads(graph, hi, fill_background)
    if lo > hi:
        lo, hi = hi, lo

    best = (abs(n_lo - target_count), lo, n_lo)
    for cand, n in ((hi, n_hi),):
        if abs(n - target_count) < best[0]:
            best = (abs(n - target_count), cand, n)

    for _ in range(max_iter):
        mid = (lo * hi) ** 0.5
        n = estimate_quads(graph, mid, fill_background)
        err = abs(n - target_count)
        if err < best[0]:
            best = (err, mid, n)
        if target_count and err <= target_count * tol:
            break
        if n > target_count:
            lo = mid
        else:
            hi = mid
        if hi / max(lo, 1e-12) < 1.0001:
            break
    return best[1], best[2]


_FILL_CACHE = {}
_FILL_CACHE_CAP = 8192
_COUNT_CACHE = {}
_COUNT_CACHE_CAP = 64


def _fill_cached(side_pts, relax_iters, project, cache_token):
    """fill_patch, memoised on the patch's boundary geometry.

    Filling is Coons plus relax iterations with a BVH reprojection per pass —
    the bulk of a rebuild — and on an incremental edit almost every patch's
    boundary is exactly what it was last time. The key is the rounded boundary
    itself plus the surface's identity token, so a patch refills only when
    something that feeds its fill actually changed.
    """
    if cache_token is None:
        return fill_patch(side_pts, relax_iters=relax_iters, project=project)
    blob = np.round(np.vstack([np.asarray(s) for s in side_pts]), 4).tobytes()
    key = (hash(blob), tuple(len(s) for s in side_pts), relax_iters,
           cache_token)
    hit = _FILL_CACHE.get(key)
    if hit is not None:
        return hit
    res = fill_patch(side_pts, relax_iters=relax_iters, project=project)
    if len(_FILL_CACHE) >= _FILL_CACHE_CAP:
        _FILL_CACHE.clear()
    _FILL_CACHE[key] = res
    return res


def build(graph, target_edge=None, project=None, relax_iters=20,
          fill_background=False, cache_token=None):
    """Returns (verts (N,3), quads, provenance, report).

    ``provenance[i]`` says where vertex i came from — a node, a point along an
    arc, or a parameterised point inside a patch. It is what the delta layer
    keys hand edits on, so an edit survives a rebuild instead of being keyed to
    a vertex index that means something different next time.
    """
    target_edge = target_edge or graph.settings.get("target_edge", 0.1)

    # Solve over representatives: a mirrored arc copies its source's count.
    # The mirrored layout has a mirrored constraint system, so solving the
    # reduced system solves both halves at once — and the two sides come out
    # bit-identical rather than merely similar.
    counts, qrep = _solve_counts(graph, target_edge, fill_background)
    for a in graph.arcs:
        graph.arcs[a].n = counts[a]

    verts = []
    prov = []
    node_vert = {}

    def add(pt, tag):
        prov.append(tag)
        return _add(pt)

    def _add(pt):
        verts.append(np.asarray(pt, dtype=float))
        return len(verts) - 1

    for nid, node in graph.nodes.items():
        node_vert[nid] = add(node.co, ("n", int(nid)))

    # arc vertices: endpoints are the shared node vertices, interior is new
    arc_verts = {}
    for aid in graph.arcs:
        arc = graph.arcs[aid]
        pts = resample(arc.path, counts[aid], project=project)
        ids = [node_vert[arc.a]]
        for k in range(1, counts[aid]):
            ids.append(add(pts[k], ("a", int(aid), k / counts[aid])))
        ids.append(node_vert[arc.b])
        verts[ids[0]] = np.asarray(graph.nodes[arc.a].co, dtype=float)
        verts[ids[-1]] = np.asarray(graph.nodes[arc.b].co, dtype=float)
        arc_verts[aid] = ids

    quads = []
    quad_patch = []
    quad_lattice = []
    charts = {}
    failed = []
    holes = []
    background = set() if fill_background else background_patches(graph)
    for pid, patch in graph.patches.items():
        if patch.fill == "hole":
            holes.append(pid)
            continue
        if pid in background:
            failed.append((pid, "background"))
            continue
        if pid in qrep["unsatisfied_patches"]:
            failed.append((pid, "unquantized"))
            continue

        side_ids, side_pts = [], []
        for side in patch.sides:
            ids = []
            for aid, reversed_ in side:
                seq = arc_verts[aid][::-1] if reversed_ else arc_verts[aid]
                ids.extend(seq if not ids else seq[1:])
            side_ids.append(ids)
            side_pts.append([verts[i] for i in ids])

        res = _fill_cached(side_pts, relax_iters, project, cache_token)
        if res is None:
            failed.append((pid, "no valid split"))
            continue
        loc_verts, loc_quads, slots, params, chart = res

        remap = {}
        for (_, si, k), loc in slots.items():
            remap[loc] = side_ids[si][k]
        for loc in range(len(loc_verts)):
            if loc not in remap:
                if params is not None and loc in params:
                    u, v = params[loc]
                    tag = ("p", int(pid), float(u), float(v))
                else:
                    tag = ("q", int(pid), int(loc))
                remap[loc] = add(loc_verts[loc], tag)
        patch_quads = [tuple(remap[i] for i in q) for q in loc_quads]
        if _would_be_nonmanifold(quads, patch_quads):
            # A patch whose fill collides with an already-placed one means the
            # layout was mis-traversed. Emitting it would hand the artist a
            # broken mesh that looks fine until they subdivide; refusing it and
            # naming the patch is the honest failure.
            failed.append((pid, "non-manifold"))
            continue
        quads.extend(patch_quads)
        quad_patch.extend([pid] * len(patch_quads))
        charts[pid] = chart
        quad_lattice.extend(chart["lattice"])

    used = {i for q in quads for i in q}
    keep = sorted(used)
    compact = {old: new for new, old in enumerate(keep)}
    out_verts = np.array([verts[i] for i in keep]) if keep else np.zeros((0, 3))
    out_quads = [tuple(compact[i] for i in q) for q in quads]
    out_prov = [prov[i] for i in keep]

    report = dict(qrep)
    report.update({
        "verts": len(out_verts),
        "quads": len(out_quads),
        "failed_patches": failed,
        "holes": holes,
        "background": sorted(background),
        "quad_patch": quad_patch,
        "quad_lattice": quad_lattice,
        "counts": counts,
        "charts": charts,
        "dropped_verts": len(verts) - len(out_verts),
        "target_edge": target_edge,
    })
    return out_verts, out_quads, out_prov, report


def _would_be_nonmanifold(existing, incoming):
    cnt = {}
    for q in list(existing) + list(incoming):
        for k in range(4):
            e = (q[k], q[(k + 1) % 4])
            e = e if e[0] < e[1] else (e[1], e[0])
            cnt[e] = cnt.get(e, 0) + 1
            if cnt[e] > 2:
                return True
    return False


def mesh_stats(verts, quads):
    """Cheap structural sanity: shared-edge counts and non-manifold detection."""
    cnt = {}
    for q in quads:
        for k in range(4):
            e = (q[k], q[(k + 1) % 4])
            e = e if e[0] < e[1] else (e[1], e[0])
            cnt[e] = cnt.get(e, 0) + 1
    return {
        "verts": len(verts),
        "quads": len(quads),
        "edges": len(cnt),
        "boundary_edges": sum(1 for c in cnt.values() if c == 1),
        "nonmanifold_edges": sum(1 for c in cnt.values() if c > 2),
        "euler": len(verts) - len(cnt) + len(quads),
    }
