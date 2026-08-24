"""Brush math for massaging the generated mesh (bpy-free, SPEC §10).

The TopoGun feel: grab the result and push it around with soft falloff, or
relax a region — always constrained to the reference surface, and always
feeding the delta layer so the edit survives every rebuild. This module is
the arithmetic; the modal operator owns events and capture.
"""

from __future__ import annotations

import numpy as np


def falloff(dists, radius):
    """Smoothstep weight per vertex: 1 at the centre, 0 at the rim."""
    t = np.clip(1.0 - np.asarray(dists, dtype=float) / max(radius, 1e-12),
                0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def vert_adjacency(quads, n_verts):
    """Neighbour index lists per vertex, from quad edges."""
    nbrs = [set() for _ in range(n_verts)]
    for q in quads:
        k = len(q)
        for i in range(k):
            a, b = q[i], q[(i + 1) % k]
            nbrs[a].add(b)
            nbrs[b].add(a)
    return [sorted(s) for s in nbrs]


def tweak(verts, center, radius, delta):
    """Move vertices under the brush by ``delta``, faded by falloff."""
    verts = np.asarray(verts, dtype=float)
    d = np.linalg.norm(verts - np.asarray(center, dtype=float), axis=1)
    w = falloff(d, radius)
    hit = w > 0.0
    out = verts.copy()
    out[hit] += np.asarray(delta, dtype=float)[None, :] * w[hit, None]
    return out, hit


def relax(verts, nbrs, center, radius, strength=0.5):
    """Pull vertices under the brush toward their neighbour average.

    One gentle step per call — the operator applies it per mouse-move, so
    holding the brush over a region keeps softening it.
    """
    verts = np.asarray(verts, dtype=float)
    d = np.linalg.norm(verts - np.asarray(center, dtype=float), axis=1)
    w = falloff(d, radius) * float(strength)
    hit = np.where(w > 0.0)[0]
    out = verts.copy()
    for i in hit:
        nb = nbrs[i]
        if not nb:
            continue
        mean = verts[nb].mean(axis=0)
        out[i] += (mean - verts[i]) * w[i]
    return out, w > 0.0
