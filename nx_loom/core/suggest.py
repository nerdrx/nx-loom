"""The organic suggestion lane (SPEC §7): proposed arcs from a cross field.

A 4-RoSy direction field is smoothed over the reference, anchored to
principal curvature directions where the surface is decisively bent;
singularities of the field mark where poles belong, and the separatrices
running out of them are exactly the arcs a retopologist would draw. They are
emitted as *suggestions* — ghost polylines the artist accepts or discards —
never as geometry, per the frozen manual-first contract.

Pure numpy. The maths is deliberately compact rather than exhaustive: the
output is a first draft for a human, and being ninety percent right is the
design target, not a compromise.
"""

from __future__ import annotations

import numpy as np


def face_frames(verts, tris):
    """Orthonormal tangent frame (e1, e2, normal) per face."""
    v = verts[tris]
    e1 = v[:, 1] - v[:, 0]
    n = np.cross(e1, v[:, 2] - v[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln < 1e-20] = 1.0
    n = n / ln
    l1 = np.linalg.norm(e1, axis=1, keepdims=True)
    l1[l1 < 1e-20] = 1.0
    e1 = e1 / l1
    e2 = np.cross(n, e1)
    return e1, e2, n


def adjacency(tris):
    """(face_a, face_b, shared edge verts) for every interior edge."""
    owner = {}
    pairs = []
    for f, tri in enumerate(tris):
        for k in range(3):
            a, b = tri[k], tri[(k + 1) % 3]
            key = (a, b) if a < b else (b, a)
            if key in owner:
                pairs.append((owner[key], f, key))
            else:
                owner[key] = f
    return pairs


def curvature_alignment(verts, tris, e1, e2, n):
    """Per-face preferred 4-RoSy direction and its confidence.

    The Cohen-Steiner/Morvan edge tensor: each edge contributes its dihedral
    bend along its own direction. The tensor's principal direction in the
    tangent plane is where loops want to run; the anisotropy |k1 - k2| says
    how much the surface actually cares.
    """
    m = len(tris)
    T = np.zeros((m, 2, 2))
    pairs = adjacency(tris)
    for fa, fb, (a, b) in pairs:
        e = verts[b] - verts[a]
        ln = float(np.linalg.norm(e))
        if ln < 1e-12:
            continue
        d = e / ln
        beta = float(np.arcsin(np.clip(np.dot(np.cross(n[fa], n[fb]), d),
                                       -1.0, 1.0)))
        for f in (fa, fb):
            x = float(np.dot(d, e1[f]))
            y = float(np.dot(d, e2[f]))
            outer = np.array([[x * x, x * y], [x * y, y * y]])
            T[f] += 0.5 * beta * ln * outer

    z0 = np.zeros(m, dtype=complex)
    weight = np.zeros(m)
    for f in range(m):
        w, vec = np.linalg.eigh(T[f])
        aniso = abs(float(w[1] - w[0]))
        # the MINIMUM-curvature direction is along the bend's axis; loops run
        # around the bend, so anchor to the max-|eigenvalue| direction
        idx = int(np.argmax(np.abs(w)))
        ang = float(np.arctan2(vec[1, idx], vec[0, idx]))
        z0[f] = np.exp(4j * ang)
        weight[f] = aniso
    if weight.max() > 0:
        weight = weight / weight.max()
    return z0, weight


def smooth_field(verts, tris, iters=60, anchor=0.5):
    """Smoothed 4-RoSy field as an angle per face (in its own frame)."""
    e1, e2, n = face_frames(verts, tris)
    pairs = adjacency(tris)
    m = len(tris)

    # transport angle from face b's frame into face a's, across each edge
    rot = np.zeros(len(pairs))
    for i, (fa, fb, _e) in enumerate(pairs):
        # common edge direction expressed in both frames
        ang_a = np.arctan2(np.dot(e2[fa], e1[fb]), np.dot(e1[fa], e1[fb]))
        rot[i] = ang_a

    z0, w0 = curvature_alignment(verts, tris, e1, e2, n)
    z = z0.copy()
    z[np.abs(z) < 1e-12] = 1.0

    # flat gather arrays: the per-face Python loop was O(faces x iters) in
    # interpreter time — an avatar-scale field would take half a minute
    src_idx = np.array([fb for fa, fb, _e in pairs]
                       + [fa for fa, fb, _e in pairs], dtype=int)
    dst_idx = np.array([fa for fa, fb, _e in pairs]
                       + [fb for fa, fb, _e in pairs], dtype=int)
    phase = np.exp(4j * np.concatenate([rot, -rot]))

    for _ in range(iters):
        acc = np.zeros(m, dtype=complex)
        np.add.at(acc, dst_idx, z[src_idx] * phase)
        acc += anchor * w0 * z0
        ln = np.abs(acc)
        ln[ln < 1e-12] = 1.0
        z = acc / ln
    theta = np.angle(z) / 4.0
    return theta, (e1, e2, n), pairs


def singularities(tris, theta, frames, pairs):
    """Vertices where the field's index is non-zero — where poles belong."""
    e1, e2, n = frames
    m = len(tris)
    # per-pair angle mismatch, snapped to the nearest quarter turn
    edge_delta = {}
    for fa, fb, key in pairs:
        ang_a = np.arctan2(np.dot(e2[fa], e1[fb]), np.dot(e1[fa], e1[fb]))
        d = theta[fb] + ang_a - theta[fa]
        snapped = d - np.round(d / (np.pi / 2)) * (np.pi / 2)
        edge_delta[(fa, fb)] = (d, d - snapped)
        edge_delta[(fb, fa)] = (-d, -(d - snapped))

    # faces around each vertex, ordered
    vert_faces = {}
    for f, tri in enumerate(tris):
        for v in tri:
            vert_faces.setdefault(int(v), []).append(f)

    face_edges = {}
    for fa, fb, key in pairs:
        face_edges.setdefault(fa, []).append((fb, key))
        face_edges.setdefault(fb, []).append((fa, key))

    out = {}
    for v, faces in vert_faces.items():
        fset = set(faces)
        # walk the ring
        start = faces[0]
        ring = [start]
        cur = start
        ok = True
        while True:
            nxt = None
            for g, key in face_edges.get(cur, ()):
                if g in fset and g not in ring and v in key:
                    nxt = g
                    break
            if nxt is None:
                break
            ring.append(nxt)
            cur = nxt
        closed = False
        for g, key in face_edges.get(cur, ()):
            if g == start and v in key and len(ring) > 2:
                closed = True
        if not closed or len(ring) < 3:
            continue
        total = 0.0
        for i in range(len(ring)):
            fa, fb = ring[i], ring[(i + 1) % len(ring)]
            d = edge_delta.get((fa, fb))
            if d is None:
                ok = False
                break
            raw, _snap = d
            total += raw - np.round(raw / (np.pi / 2)) * (np.pi / 2)
        if not ok:
            continue
        index = int(np.round(total / (np.pi / 2)))
        if index != 0:
            out[v] = index
    return out


def face_neighbors(tris):
    """1-ring face adjacency through shared vertices (robust at fans)."""
    by_vert = {}
    for f, tri in enumerate(tris):
        for v in tri:
            by_vert.setdefault(int(v), []).append(f)
    nbrs = [set() for _ in range(len(tris))]
    for faces in by_vert.values():
        for f in faces:
            nbrs[f].update(faces)
    return [np.fromiter(x, dtype=int) for x in nbrs]


def trace(verts, tris, theta, frames, start_face, direction, step, max_len,
          stop_fn=None, nbrs=None):
    """March a streamline of the field until something stops it.

    Stepping is CONNECTIVITY-CONSTRAINED: the next face is searched only among
    the current face's neighbourhood. A global nearest-face search teleports
    across gaps on thin features — between two toes, between fingers — and a
    trace that jumps through air turns a foot into a knot of floating lines.
    Losing the surface locally ends the trace instead.
    """
    e1, e2, n = frames
    tri_centers = verts[tris].mean(axis=1)
    tri_size = float(np.median(np.linalg.norm(
        verts[tris[:, 1]] - verts[tris[:, 0]], axis=1)))
    if nbrs is None:
        nbrs = face_neighbors(tris)

    p = tri_centers[start_face].copy()
    d = direction / max(np.linalg.norm(direction), 1e-12)
    pts = [p.copy()]
    travelled = 0.0
    f = start_face
    while travelled < max_len:
        base = theta[f]
        best, bestdot = None, -2.0
        for k in range(4):
            cand = (np.cos(base + k * np.pi / 2) * e1[f]
                    + np.sin(base + k * np.pi / 2) * e2[f])
            dot = float(np.dot(cand, d))
            if dot > bestdot:
                bestdot, best = dot, cand
        d = best
        p = p + d * step
        # candidate faces: the local ring, plus their ring (two hops)
        ring = nbrs[f]
        cand_faces = np.unique(np.concatenate([nbrs[g] for g in ring]))
        dists = np.linalg.norm(tri_centers[cand_faces] - p, axis=1)
        j = int(np.argmin(dists))
        if float(dists[j]) > max(step, tri_size) * 2.5:
            break                     # lost the surface — end, do not teleport
        f = int(cand_faces[j])
        p = p - n[f] * float(np.dot(p - tri_centers[f], n[f]))
        pts.append(p.copy())
        travelled += step
        if stop_fn is not None and stop_fn(p, travelled):
            break
    return np.asarray(pts)


def smooth_proxy(verts, tris, iters=6):
    """Taubin-style smoothing of the proxy before field solving.

    A decimated sculpt keeps every crease and nub of the toes; the field
    inherits that chaos as singularity storms. The suggestions are about the
    broad flow, not the nubs, so the field deserves a calmer surface than the
    render does. Taubin's shrink/expand pair keeps the volume from collapsing.
    """
    verts = np.asarray(verts, dtype=float).copy()
    tris = np.asarray(tris, dtype=int)
    nbr = {}
    for tri in tris:
        for k in range(3):
            a, b = int(tri[k]), int(tri[(k + 1) % 3])
            nbr.setdefault(a, set()).add(b)
            nbr.setdefault(b, set()).add(a)
    idx = [np.fromiter(nbr.get(i, {i}), dtype=int) for i in range(len(verts))]
    for _ in range(iters):
        for lam in (0.5, -0.53):
            mean = np.array([verts[j].mean(axis=0) for j in idx])
            verts = verts + lam * (mean - verts)
    return verts


def _cluster(points, radius):
    """Greedy clustering; returns representative indices."""
    reps = []
    for i, p in enumerate(points):
        if all(np.linalg.norm(points[r] - p) > radius for r in reps):
            reps.append(i)
    return reps


def suggest(verts, tris, spacing=None, max_traces=48, presmooth=True):
    """Separatrix suggestions. -> (polylines, singular_points)."""
    from .budget import drain
    return drain(suggest_iter(verts, tris, spacing=spacing,
                              max_traces=max_traces, presmooth=presmooth))


def suggest_iter(verts, tris, spacing=None, max_traces=48, presmooth=True):
    """suggest() as a progress generator — yields (fraction, label) between
    the field stages and between traces so a background job can keep the UI
    alive and honour a Cancel mid-way.

    Quality gates, learned from a real avatar: singularities cluster in
    high-detail areas (toes), so only cluster representatives spawn traces;
    traces shorter than a few steps are dropped; and the connectivity-
    constrained tracer ends a trace rather than jumping between shells.
    """
    verts = np.asarray(verts, dtype=float)
    tris = np.asarray(tris, dtype=int)
    yield (0.05, "smoothing the proxy")
    field_verts = smooth_proxy(verts, tris) if presmooth else verts
    yield (0.15, "solving the cross field")
    theta, frames, pairs = smooth_field(field_verts, tris)
    yield (0.45, "finding field poles")
    sing = singularities(tris, theta, frames, pairs)
    e1, e2, n = frames

    span = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0)))
    step = span * 0.02
    max_len = span * 0.8
    keep_out = span * 0.04
    min_pts = 8

    sing_ids = list(sing.keys())
    sing_pts = verts[sing_ids] if sing_ids else np.zeros((0, 3))
    reps = _cluster(sing_pts, keep_out) if len(sing_pts) else []
    nbrs = face_neighbors(tris)

    vert_faces = {}
    for f, tri in enumerate(tris):
        for v in tri:
            vert_faces.setdefault(int(v), []).append(f)

    polylines = []
    laid = []

    def stop_fn(p, travelled):
        if travelled < keep_out * 2:
            return False
        if len(sing_pts) and np.linalg.norm(sing_pts - p, axis=1).min() \
                < keep_out:
            return True
        for poly in laid:
            if np.linalg.norm(poly - p, axis=1).min() < keep_out * 0.75:
                return True
        return False

    for ri, r in enumerate(reps):
        if len(polylines) >= max_traces:
            break
        yield (0.5 + 0.5 * ri / max(len(reps), 1),
               f"tracing flow {ri + 1}/{len(reps)}")
        v = sing_ids[r]
        f0 = vert_faces[v][0]
        base = theta[f0]
        for k in range(4):
            if len(polylines) >= max_traces:
                break
            d0 = (np.cos(base + k * np.pi / 2) * e1[f0]
                  + np.sin(base + k * np.pi / 2) * e2[f0])
            poly = trace(field_verts, tris, theta, frames, f0, d0, step,
                         max_len, stop_fn, nbrs=nbrs)
            if len(poly) >= min_pts:
                poly[0] = field_verts[v]
                polylines.append(poly)
                laid.append(poly)
    return polylines, sing_pts
