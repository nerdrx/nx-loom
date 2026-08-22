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


def ray_hits(surface, origin, direction, max_hits=6):
    """Every surface crossing along a ray, near to far.

    One ray through a limb hits the near wall, the far wall, and whatever is
    behind it. Taking the first hit is not enough on its own, but having all of
    them is what lets :func:`trace_rays` pick the sheet the stroke is actually
    on.
    """
    if not (np.all(np.isfinite(origin)) and np.all(np.isfinite(direction))):
        return []
    d = Vector(tuple(direction)).normalized()
    span = float(np.linalg.norm(surface.verts.max(axis=0) - surface.verts.min(axis=0))) \
        if len(surface.verts) else 1.0
    eps = max(span * 1e-6, 1e-9)
    cur = Vector(tuple(origin))
    out = []
    for _ in range(max_hits):
        loc, _, _, _ = surface.tree.ray_cast(cur, d)
        if loc is None:
            break
        p = np.array(loc[:], dtype=float)
        if not np.all(np.isfinite(p)):
            break
        out.append(p)
        cur = loc + d * eps
    return out


def trace_rays(surface, rays, min_step=0.0, anchor=None, max_hits=6):
    """Surface path from a sequence of (origin, direction) rays.

    Takes the nearest hit — you are drawing on what you can see — and falls
    back to a deeper crossing only when the nearest one would tear the stroke.
    Choosing purely by shortest total path is wrong: a flat wall behind the
    model beats curving around the limb in front of it, and the stroke jumps
    to the far surface.

    Rays that miss entirely are dropped rather than guessed at — a stroke that
    runs off the silhouette should stop there.
    """
    cands = []
    for origin, direction in rays:
        hits = ray_hits(surface, origin, direction, max_hits=max_hits)
        if hits:
            cands.append(hits)
    if not cands:
        return np.zeros((0, 3))

    nearest = [h[0] for h in cands]
    steps = [float(np.linalg.norm(nearest[i] - nearest[i - 1]))
             for i in range(1, len(nearest))]
    span = float(np.linalg.norm(surface.verts.max(axis=0) - surface.verts.min(axis=0))) \
        if len(surface.verts) else 1.0
    if steps:
        median = float(np.median(steps))
        max_jump = max(median * 5.0, span * 0.02)
    else:
        max_jump = span

    pts = []
    prev = np.asarray(anchor, dtype=float) if anchor is not None else None
    for hits in cands:
        if prev is None:
            p = hits[0]
        else:
            p = hits[0]
            if float(np.linalg.norm(p - prev)) > max_jump:
                p = min(hits, key=lambda h: float(np.linalg.norm(h - prev)))
        prev = p
        if min_step > 0.0 and pts and np.linalg.norm(p - pts[-1]) < min_step:
            continue
        pts.append(p)

    if len(pts) < 2 and len(cands) >= 2:
        pts = [cands[0][0], cands[-1][0]]
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
