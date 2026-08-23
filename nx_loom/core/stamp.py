"""Topology stamps: reusable layout fragments (bpy-free, SPEC §10).

The feature a veteran actually builds over years: the eye ring, the mouth
loops, the pole fan they have drawn a hundred times, kept as a library and
dropped onto the next model in one gesture. A stamp is a set of 2D polylines
in a unit disc; placing one maps it onto the surface's tangent plane and
projects it down. Placed stamps arrive as suggestion GHOSTS — the same
accept/discard lane as the field suggestions, so a stamp is never applied on
its own (SPEC §7) and inherits snapping, crossings and mirroring on accept.

Rings are emitted as four quarter arcs, not one closed loop: the quarter
endpoints become shared nodes on accept, which gives every ring its four
corners — the same reason ring cuts commit as ``ring_segments``.
"""

from __future__ import annotations

import numpy as np


def _quarter_rings(r, aspect=1.0, seg=8, rot=0.0):
    polys = []
    for q in range(4):
        ts = np.linspace(q * np.pi / 2, (q + 1) * np.pi / 2, seg + 1) + rot
        polys.append(np.stack([np.cos(ts) * r,
                               np.sin(ts) * r * aspect], axis=1))
    return polys


def _spoke(angle, r0, r1, aspect0=1.0, aspect1=1.0, seg=6):
    ts = np.linspace(0.0, 1.0, seg + 1)
    x0, y0 = np.cos(angle) * r0, np.sin(angle) * r0 * aspect0
    x1, y1 = np.cos(angle) * r1, np.sin(angle) * r1 * aspect1
    return np.stack([x0 + (x1 - x0) * ts, y0 + (y1 - y0) * ts], axis=1)


def _builtin_eye():
    """Two concentric rings plus four spokes — the classic eye socket:
    the inner ring is the lid rim, the outer absorbs the flow around it."""
    polys = _quarter_rings(0.5) + _quarter_rings(1.0)
    for q in range(4):
        polys.append(_spoke(q * np.pi / 2, 0.5, 1.0))
    return polys


def _builtin_mouth():
    """Two nested wide ellipses with spokes at the corners and mid lips."""
    polys = _quarter_rings(0.55, aspect=0.4) + _quarter_rings(1.0, aspect=0.55)
    for q in range(4):
        polys.append(_spoke(q * np.pi / 2, 0.55, 1.0,
                            aspect0=0.4, aspect1=0.55))
    return polys


def _builtin_pole_fan():
    """A quartered ring with four spokes meeting at the centre — the honest
    way to end four lanes of flow in one place."""
    polys = _quarter_rings(1.0)
    for q in range(4):
        polys.append(_spoke(q * np.pi / 2, 0.0, 1.0))
    return polys


BUILTINS = {
    "eye": _builtin_eye,
    "mouth": _builtin_mouth,
    "pole_fan": _builtin_pole_fan,
}


def builtin(name):
    return [np.asarray(p, dtype=float) for p in BUILTINS[name]()]


def place(polys2d, origin, e1, e2, scale, rot=0.0, project=None):
    """Map unit-disc polylines onto the tangent frame at ``origin``.

    Returns a list of (N,3) world-space polylines, optionally projected onto
    the surface so the stamp hugs the sculpt instead of floating on the
    tangent plane.
    """
    origin = np.asarray(origin, dtype=float)
    e1 = np.asarray(e1, dtype=float)
    e2 = np.asarray(e2, dtype=float)
    c, s = np.cos(rot), np.sin(rot)
    out = []
    for poly in polys2d:
        p = np.asarray(poly, dtype=float)
        x = p[:, 0] * c - p[:, 1] * s
        y = p[:, 0] * s + p[:, 1] * c
        pts = origin[None, :] + x[:, None] * e1 * scale \
            + y[:, None] * e2 * scale
        if project is not None:
            pts = np.asarray(project(pts), dtype=float)
        out.append(pts)
    return out


def normalize(polys3d):
    """Flatten captured 3D arcs into a saveable unit-disc stamp.

    Fits the best plane through all points (PCA), projects onto it, centres
    and scales to unit radius. Curvature is deliberately discarded — a stamp
    is intent, and placing re-projects it onto whatever surface it lands on.
    """
    all_pts = np.concatenate([np.asarray(p, dtype=float) for p in polys3d])
    centre = all_pts.mean(axis=0)
    d = all_pts - centre
    _w, v = np.linalg.eigh(d.T @ d)
    e1, e2 = v[:, 2], v[:, 1]          # the two dominant directions
    out = []
    radius = 0.0
    for poly in polys3d:
        p = np.asarray(poly, dtype=float) - centre
        uv = np.stack([p @ e1, p @ e2], axis=1)
        radius = max(radius, float(np.linalg.norm(uv, axis=1).max()))
        out.append(uv)
    if radius <= 0.0:
        return None
    return [(p / radius).tolist() for p in out]
