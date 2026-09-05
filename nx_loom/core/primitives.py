"""Primitive detection: what the surface is BETWEEN its creases (bpy-free).

Hard-surface references are planes and cylinders wearing noise. Segmenting
the proxy into smooth regions (creases as walls) and fitting each region
tells the tool the *ideal* shape: a cylinder region yields an exact axis and
radius — rings proposed from it are mathematically round, not traced — and a
plane region is what per-patch Flatten projects onto. A region neither fit
explains stays honestly "free".
"""

from __future__ import annotations

import numpy as np

from .suggest import adjacency, face_frames


def segment_regions(verts, tris, angle_deg=30.0, min_faces=8):
    """Connected smooth regions; sharp DIHEDRAL edges are the walls.

    Growing across face adjacency and refusing to cross any edge sharper
    than the threshold is deliberately not the magnet's relative-contrast
    gate: a cylinder's own curvature would drown its rims there, but a rim
    is a wall regardless of how bendy the wall's inside is.
    """
    verts = np.asarray(verts, dtype=float)
    tris = np.asarray(tris, dtype=int)
    _e1, _e2, n = face_frames(verts, tris)
    cos_t = np.cos(np.radians(angle_deg))
    links = [[] for _ in range(len(tris))]
    for fa, fb, _e in adjacency(tris):
        if float(n[fa] @ n[fb]) > cos_t:
            links[fa].append(fb)
            links[fb].append(fa)
    seen = np.zeros(len(tris), dtype=bool)
    regions = []
    for f0 in range(len(tris)):
        if seen[f0]:
            continue
        stack, region = [f0], []
        seen[f0] = True
        while stack:
            f = stack.pop()
            region.append(f)
            for g in links[f]:
                if not seen[g]:
                    seen[g] = True
                    stack.append(g)
        if len(region) >= min_faces:
            regions.append(np.asarray(region, dtype=int))
    return regions


def fit_plane(pts):
    """(centroid, normal, mean |distance|)."""
    pts = np.asarray(pts, dtype=float)
    c = pts.mean(axis=0)
    d = pts - c
    _w, v = np.linalg.eigh(d.T @ d)
    n = v[:, 0]
    return c, n, float(np.abs(d @ n).mean())


def fit_cylinder(pts, normals):
    """(axis, centre, radius, mean radial residual).

    A cylinder's face normals all lie in the plane perpendicular to its
    axis, so the axis is the direction the normals DON'T span — the
    smallest eigenvector of their covariance. Centre and radius then come
    from a Kasa circle fit in the axis plane.
    """
    pts = np.asarray(pts, dtype=float)
    normals = np.asarray(normals, dtype=float)
    _w, v = np.linalg.eigh(normals.T @ normals)
    axis = v[:, 0]
    e1 = np.array([1.0, 0.0, 0.0])
    if abs(float(e1 @ axis)) > 0.9:
        e1 = np.array([0.0, 1.0, 0.0])
    e1 = e1 - axis * float(e1 @ axis)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(axis, e1)
    x = pts @ e1
    y = pts @ e2
    A = np.stack([x, y, np.ones_like(x)], axis=1)
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0] / 2.0, sol[1] / 2.0
    r2 = sol[2] + cx * cx + cy * cy
    if r2 <= 0.0:
        return axis, pts.mean(axis=0), 0.0, np.inf
    r = float(np.sqrt(r2))
    centre = cx * e1 + cy * e2 + axis * float((pts @ axis).mean())
    resid = float(np.abs(np.sqrt((x - cx) ** 2 + (y - cy) ** 2) - r).mean())
    return axis, centre, r, resid


def detect(verts, tris, min_faces=8):
    """Regions with their best explanation: plane, cylinder, or free."""
    verts = np.asarray(verts, dtype=float)
    tris = np.asarray(tris, dtype=int)
    span = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0)))
    if span <= 0.0:
        return []
    _e1, _e2, n = face_frames(verts, tris)
    centers = verts[tris].mean(axis=1)
    out = []
    for region in segment_regions(verts, tris, min_faces=min_faces):
        pts = centers[region]
        c, pn, p_resid = fit_plane(pts)
        if p_resid < span * 0.005:
            out.append({"faces": region, "kind": "plane",
                        "centre": c, "normal": pn, "residual": p_resid})
            continue
        axis, cc, r, c_resid = fit_cylinder(pts, n[region])
        if np.isfinite(c_resid) and c_resid < span * 0.01 and r < span:
            out.append({"faces": region, "kind": "cylinder",
                        "axis": axis, "centre": cc, "radius": r,
                        "residual": c_resid})
            continue
        out.append({"faces": region, "kind": "free", "residual": p_resid})
    return out
