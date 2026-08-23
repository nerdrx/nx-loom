"""Quad quality: see the stretch before you Apply (bpy-free, SPEC §10).

Per-quad scalar in [0, 1]: 1 is a square, and the score is the WORSE of two
independent sins — stretch (shortest/longest edge) and shear (how far the
corners sit from 90 degrees). Taking the minimum rather than a blend keeps
the score honest: a quad that is perfect in one way and broken in the other
is broken.
"""

from __future__ import annotations

import numpy as np


def quad_quality(verts, quads):
    """(m,) quality per quad. Degenerate quads score 0."""
    verts = np.asarray(verts, dtype=float)
    q = np.asarray(quads, dtype=int)
    if not len(q):
        return np.zeros(0)
    v = verts[q]                                   # (m, 4, 3)
    edges = v[:, [1, 2, 3, 0]] - v                 # (m, 4, 3)
    ln = np.linalg.norm(edges, axis=2)             # (m, 4)
    lo, hi = ln.min(axis=1), ln.max(axis=1)
    ok = hi > 1e-12
    stretch = np.where(ok, lo / np.where(ok, hi, 1.0), 0.0)

    d = edges / np.where(ln[..., None] > 1e-12, ln[..., None], 1.0)
    # corner k is between edge k-1 arriving and edge k leaving
    cosang = np.abs((-d[:, [3, 0, 1, 2]] * d).sum(axis=2))   # (m, 4)
    shear = 1.0 - cosang.max(axis=1)

    return np.clip(np.minimum(stretch, shear), 0.0, 1.0)
