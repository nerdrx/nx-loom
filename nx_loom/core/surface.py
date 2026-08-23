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

    _SERIAL = 0

    def __init__(self, obj, depsgraph=None):
        Surface._SERIAL += 1
        # identity token for caches keyed on "same reference, unchanged":
        # id() can be recycled after garbage collection, a serial cannot
        self.token = Surface._SERIAL
        # Only the name is kept. A cached Surface can outlive a file load, and
        # holding a reference to a freed datablock is a crash waiting to be
        # dereferenced.
        self.name = obj.name
        depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        bm = bmesh.new()
        bm.from_mesh(eval_obj.to_mesh())
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        bm.transform(obj.matrix_world)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        self.verts = np.array([v.co[:] for v in bm.verts], dtype=float)
        # Smooth (area-averaged) vertex normals. At a sharp rim the *face*
        # normal is whichever facet the nearest-point query landed on, which
        # makes the tangent plane there arbitrary; the smooth normal is the
        # bisector of the facets meeting at the rim and is well defined.
        self.vnormals = np.array([v.normal[:] for v in bm.verts], dtype=float)
        self.tris = np.array([[l.vert.index for l in f.loops] for f in bm.faces], dtype=int)
        # Cached: ray_hits needs it per ray, and recomputing a min/max over
        # every vertex per ray made drawing on a dense sculpt unusable.
        self.span = float(np.linalg.norm(self.verts.max(axis=0) - self.verts.min(axis=0))) \
            if len(self.verts) else 1.0
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
        """Smooth surface normal, barycentrically interpolated.

        Used to orient the tangent plane the layout's rotation system is sorted
        in, so it has to stay continuous across a crease — a face normal does
        not, and a node sitting on a rim then gets a scrambled arc order and
        sends the patch traversal through the wrong arc.
        """
        hit, nrm, tri, _ = self.tree.find_nearest(Vector(co))
        if hit is None:
            return np.array([0.0, 0.0, 1.0])
        idx = self.tris[int(tri) % len(self.tris)]
        a, b, c = self.verts[idx]
        u, v = _barycentric(np.asarray(hit[:]), a, b, c)
        w = 1.0 - u - v
        smooth = (self.vnormals[idx[0]] * w + self.vnormals[idx[1]] * u
                  + self.vnormals[idx[2]] * v)
        ln = np.linalg.norm(smooth)
        return smooth / ln if ln > 1e-9 else np.asarray(nrm[:])

    def face_normal_at(self, co):
        _, nrm, _, _ = self.tree.find_nearest(Vector(co))
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


# -- caching ---------------------------------------------------------------
#
# Building a Surface means building a BVH over the whole reference — 148 ms on
# a 32k-vertex mesh, and far worse on a real character. Every click that erases
# an arc, drags a node or toggles a hole used to pay that, and so did every
# refresh after drawing an arc, which is what made clicking feel unresponsive.

_CACHE = {}


def _fingerprint(obj):
    me = obj.data
    # Datablock pointers first. Without them a fresh object with identical
    # geometry — a re-added primitive, anything after a file load — matches the
    # fingerprint of a *freed* one and the cache hands back a Surface built
    # over dead data. Caching bpy datablocks across a file load is never safe
    # on geometry alone.
    n = len(me.vertices)
    step = max(n // 64, 1)
    probe = []
    for i in range(0, n, step):
        co = me.vertices[i].co
        probe.append((round(co.x, 5), round(co.y, 5), round(co.z, 5)))
    return (obj.as_pointer(), me.as_pointer(), obj.data.name, n,
            len(me.polygons), len(obj.modifiers),
            tuple(round(v, 6) for row in obj.matrix_world for v in row),
            tuple(probe))


def cached_surface(obj, depsgraph=None):
    """A Surface for obj, rebuilt only when the mesh actually changed.

    The fingerprint samples up to 64 vertices rather than all of them: a full
    checksum would be cheap next to a BVH build but is still O(n) on every
    mouse click, and a sparse probe catches sculpt edits in practice.
    """
    if obj is None:
        return None
    key = _fingerprint(obj)
    hit = _CACHE.get(obj.name)
    if hit is not None and hit[0] == key:
        return hit[1]
    surf = Surface(obj, depsgraph)
    _CACHE[obj.name] = (key, surf)
    return surf


def clear_surface_cache(name=None):
    if name is None:
        _CACHE.clear()
    else:
        _CACHE.pop(name, None)
