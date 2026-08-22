"""The reference surface: BVH, barycentric pins, projection, arc resampling.

Pins are how a layout survives edits to the sculpt (SPEC §1): an arc remembers
*where on the surface* it runs, not a stale world position.
"""

from __future__ import annotations

import numpy as np

try:
    import bmesh
    import bpy
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
except ImportError:                                    # headless math-only use
    bmesh = bpy = Vector = BVHTree = None


class Surface:
    """Triangulated snapshot of a reference object, with a BVH over it."""

    def __init__(self, obj, depsgraph=None):
        self.obj = obj
        depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        bm = bmesh.new()
        bm.from_mesh(eval_obj.to_mesh())
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        bm.transform(obj.matrix_world)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        self.verts = np.array([v.co[:] for v in bm.verts], dtype=float)
        self.tris = np.array([[l.vert.index for l in f.loops] for f in bm.faces], dtype=int)
        self.tree = BVHTree.FromPolygons(
            [tuple(v) for v in self.verts], [tuple(t) for t in self.tris], all_triangles=True
        )
        bm.free()
        eval_obj.to_mesh_clear()

    # -- pins ------------------------------------------------------------

    def pin(self, co):
        """World point -> (tri_index, u, v). Returns None if the BVH misses."""
        hit, _, tri, _ = self.tree.find_nearest(Vector(co))
        if hit is None:
            return None
        a, b, c = self.verts[self.tris[tri]]
        u, v = _barycentric(np.asarray(hit), a, b, c)
        return (int(tri), float(u), float(v))

    def unpin(self, pin):
        """(tri, u, v) -> world point."""
        tri, u, v = pin
        a, b, c = self.verts[self.tris[int(tri) % len(self.tris)]]
        return a * (1.0 - u - v) + b * u + c * v

    # -- projection ------------------------------------------------------

    def project(self, points):
        """Nearest surface point for each row of an (n, 3) array."""
        pts = np.asarray(points, dtype=float)
        out = np.empty_like(pts)
        for i, p in enumerate(pts):
            hit, _, _, _ = self.tree.find_nearest(Vector(p))
            out[i] = p if hit is None else np.asarray(hit[:])
        return out

    def normal_at(self, co):
        hit, nrm, _, _ = self.tree.find_nearest(Vector(co))
        return np.array([0.0, 0.0, 1.0]) if nrm is None else np.asarray(nrm[:])


def _barycentric(p, a, b, c):
    v0, v1, v2 = b - a, c - a, p - a
    d00, d01, d11 = v0 @ v0, v0 @ v1, v1 @ v1
    d20, d21 = v2 @ v0, v2 @ v1
    den = d00 * d11 - d01 * d01
    if abs(den) < 1e-20:
        return 0.0, 0.0
    u = (d11 * d20 - d01 * d21) / den
    v = (d00 * d21 - d01 * d20) / den
    return u, v


# -- arc resampling (bpy-free) ---------------------------------------------

def polyline_length(path):
    p = np.asarray(path, dtype=float)
    if len(p) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum())


def resample(path, n, project=None):
    """Resample a polyline to exactly n segments, evenly by arc length.

    Endpoints are preserved exactly — they are shared corners and must not
    drift. Interior samples are optionally reprojected onto the surface.
    """
    p = np.asarray(path, dtype=float)
    if len(p) < 2:
        return np.repeat(p[:1], n + 1, axis=0)
    seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    if total <= 0.0:
        return np.repeat(p[:1], n + 1, axis=0)
    want = np.linspace(0.0, total, n + 1)
    out = np.empty((n + 1, 3))
    for i, d in enumerate(want):
        k = int(np.searchsorted(cum, d, side="right") - 1)
        k = min(max(k, 0), len(seg) - 1)
        t = 0.0 if seg[k] <= 0 else (d - cum[k]) / seg[k]
        out[i] = p[k] + (p[k + 1] - p[k]) * t
    out[0], out[-1] = p[0], p[-1]
    if project is not None and n > 1:
        out[1:-1] = project(out[1:-1])
    return out
