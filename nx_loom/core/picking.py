"""Turning viewport rays into points on the reference surface.

The modal tools hand this module a list of rays and get back a surface path.
Keeping the ray -> surface step here rather than inside the operator is what
makes the drawing tools testable without a viewport: a synthetic ray is just a
pair of vectors.
"""

from __future__ import annotations

import numpy as np

try:
    from mathutils import Vector
except ImportError:
    Vector = None


def ray_surface(surface, origin, direction):
    """First surface hit along a ray, in either direction. None if it misses."""
    o = Vector(tuple(origin))
    d = Vector(tuple(direction)).normalized()
    hit = surface.tree.ray_cast(o, d)
    if hit[0] is None:
        hit = surface.tree.ray_cast(o, -d)
    if hit[0] is None:
        return None
    p = np.array(hit[0][:], dtype=float)
    return p if np.all(np.isfinite(p)) else None


def trace_rays(surface, rays, min_step=0.0):
    """Surface path from a sequence of (origin, direction) rays.

    Rays that miss the surface are dropped rather than guessed at — a stroke
    that runs off the silhouette should stop at the silhouette, not jump to
    whatever happens to be behind it.
    """
    pts = []
    for origin, direction in rays:
        if not (np.all(np.isfinite(origin)) and np.all(np.isfinite(direction))):
            continue
        p = ray_surface(surface, origin, direction)
        if p is None:
            continue
        if min_step > 0.0 and pts and np.linalg.norm(p - pts[-1]) < min_step:
            continue
        pts.append(p)
    return np.array(pts, dtype=float) if pts else np.zeros((0, 3))


def interp_rays(ray_a, ray_b, steps):
    """Rays evenly interpolated between two, for a click-to-click segment.

    Interpolating in ray space rather than in world space is what makes the
    result follow the surface: each sample is re-cast, so a segment drawn
    across a bulge wraps over it instead of tunnelling through.
    """
    (o0, d0), (o1, d1) = ray_a, ray_b
    o0, d0, o1, d1 = (np.asarray(v, dtype=float) for v in (o0, d0, o1, d1))
    out = []
    for k in range(steps + 1):
        t = k / max(steps, 1)
        o = o0 + (o1 - o0) * t
        d = d0 + (d1 - d0) * t
        n = np.linalg.norm(d)
        out.append((o, d / n if n > 1e-12 else d0))
    return out


def screen_ray(region, rv3d, xy):
    """Viewport ray for a mouse position. The only untestable piece here."""
    from bpy_extras import view3d_utils
    co = (float(xy[0]), float(xy[1]))
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, co)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, co)
    return np.array(origin[:], dtype=float), np.array(direction[:], dtype=float)


def pixel_radius_world(region, rv3d, point, pixels):
    """World-space radius that subtends `pixels` on screen at `point`.

    Snapping has to feel the same whether you are zoomed into an ear or looking
    at the whole body, so the snap radius is defined in pixels and converted
    here, not fixed in world units.
    """
    from bpy_extras import view3d_utils
    p2d = view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(tuple(point)))
    if p2d is None:
        return 0.0
    off = view3d_utils.region_2d_to_location_3d(
        region, rv3d, (p2d.x + pixels, p2d.y), Vector(tuple(point))
    )
    return float(np.linalg.norm(np.array(off[:]) - np.asarray(point, dtype=float)))
