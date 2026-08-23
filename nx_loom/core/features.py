"""Feature curves: the sculpt's own ridges and valleys (bpy-free, SPEC §10).

The magnet lane: while drawing, a stroke can snap to the crease lines the
surface already has — an ear rim, a lip line, a hard-surface edge — instead
of the artist re-tracing them freehand. The curves come from the same
Cohen-Steiner/Morvan edge tensors the suggestion field anchors to, but here
the *axis* matters, not the 4-RoSy class: for a face on a crease, the bending
edges ARE the crease edges, so the tensor's dominant eigenvector runs along
the crease and chains of such faces are walked into polylines.

Honesty gate: a surface with no decisive creases (a sphere, a flat sheet)
yields NO curves rather than noise — the magnet then simply has nothing to
pull toward, which is the correct answer.
"""

from __future__ import annotations

import numpy as np

from .suggest import adjacency, face_frames, face_neighbors


def feature_field(verts, tris):
    """Per-face crease axis (3,) and bend strength, from the edge tensors."""
    verts = np.asarray(verts, dtype=float)
    tris = np.asarray(tris, dtype=int)
    e1, e2, n = face_frames(verts, tris)
    m = len(tris)
    T = np.zeros((m, 2, 2))
    for fa, fb, (a, b) in adjacency(tris):
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
            T[f] += 0.5 * abs(beta) * ln * outer

    dirs = np.zeros((m, 3))
    strength = np.zeros(m)
    for f in range(m):
        w, vec = np.linalg.eigh(T[f])
        idx = int(np.argmax(np.abs(w)))
        strength[f] = abs(float(w[idx]))
        dirs[f] = vec[0, idx] * e1[f] + vec[1, idx] * e2[f]
    return dirs, strength


def feature_curves(verts, tris, min_pts=4):
    """Chained crease polylines, or [] when the surface has none to offer."""
    verts = np.asarray(verts, dtype=float)
    tris = np.asarray(tris, dtype=int)
    if len(tris) < 4:
        return []
    dirs, w = feature_field(verts, tris)
    if float(w.max()) <= 1e-9:
        return []                    # a sheet: nothing bends at all
    # Decisive creases stand far above the surface's BACKGROUND bend — the
    # median over every face, flat ones included. A sphere bends the same
    # everywhere (median ~ max, rejected); an ideal fold on a flat sheet has
    # median 0 (everything but the fold is flat, decisively featured).
    med = float(np.median(w))
    if med > 0.0 and float(w.max()) < 6.0 * med:
        return []
    keep = w >= 0.3 * float(w.max())
    centers = verts[tris].mean(axis=1)
    nbrs = face_neighbors(tris)
    visited = np.zeros(len(tris), dtype=bool)
    curves = []

    def _walk(f0, direction):
        chain = []
        cur, d = f0, direction
        while True:
            best, best_dot = None, 0.35
            for g in nbrs[cur]:
                if not keep[g] or visited[g]:
                    continue
                step = centers[g] - centers[cur]
                ln = float(np.linalg.norm(step))
                if ln < 1e-12:
                    continue
                dot = float(step @ d) / ln
                if dot > best_dot:
                    best, best_dot = g, dot
            if best is None:
                return chain
            visited[best] = True
            step = centers[best] - centers[cur]
            d = step / float(np.linalg.norm(step))
            chain.append(centers[best])
            cur = best

    for f0 in np.argsort(-w):
        f0 = int(f0)
        if not keep[f0] or visited[f0]:
            continue
        visited[f0] = True
        fwd = _walk(f0, dirs[f0])
        back = _walk(f0, -dirs[f0])
        poly = back[::-1] + [centers[f0]] + fwd
        if len(poly) < min_pts:
            continue
        poly = np.asarray(poly, dtype=float)
        for _ in range(2):                       # light fairing
            poly[1:-1] = poly[1:-1] * 0.5 + (poly[:-2] + poly[2:]) * 0.25
        curves.append(poly)
    return curves
