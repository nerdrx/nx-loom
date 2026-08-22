"""Move a layout from one mesh onto another.

This is the thing a pinned graph can do that a mesh cannot. A retopologised
mesh is vertices — positions on one specific surface, meaningless anywhere
else. A layout is a description of *intent*: corners, edge flow, where the
creases are. Intent transfers.

The transfer is a smooth warp fitted to landmark pairs, followed by
re-projection onto the target surface and a fresh pin. The topology is not
touched at all — only where the nodes sit. What comes out is an ordinary
editable layout, so being ninety percent right is useful here, unlike a
ninety-percent-right auto-remesh.

Landmarks come from whatever the two models already agree on. For characters
that is usually an armature: two humanoid rigs share bone names, and matching
bone heads gives a dense, meaningful correspondence for free.
"""

from __future__ import annotations

import numpy as np


def thin_plate_warp(src, dst, smoothing=0.0):
    """Fit a 3D thin-plate spline mapping src points onto dst points.

    Returns a callable taking (n, 3) and returning (n, 3). With fewer than four
    landmarks there is not enough to pin down an affine part, so the fit falls
    back to the best rigid-plus-uniform-scale transform.
    """
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    if len(src) != len(dst) or len(src) == 0:
        return lambda pts: np.asarray(pts, dtype=float)
    if len(src) < 4:
        return _similarity(src, dst)

    n = len(src)
    d = np.linalg.norm(src[:, None, :] - src[None, :, :], axis=2)
    K = d.copy()                       # U(r) = r is the 3D biharmonic kernel
    if smoothing:
        K = K + np.eye(n) * smoothing
    P = np.hstack([np.ones((n, 1)), src])

    A = np.zeros((n + 4, n + 4))
    A[:n, :n] = K
    A[:n, n:] = P
    A[n:, :n] = P.T
    rhs = np.zeros((n + 4, 3))
    rhs[:n] = dst
    sol, *_ = np.linalg.lstsq(A, rhs, rcond=None)
    W, Aff = sol[:n], sol[n:]

    def warp(pts):
        pts = np.atleast_2d(np.asarray(pts, dtype=float))
        r = np.linalg.norm(pts[:, None, :] - src[None, :, :], axis=2)
        out = r @ W + np.hstack([np.ones((len(pts), 1)), pts]) @ Aff
        return out

    return warp


def _similarity(src, dst):
    """Least-squares rotation + uniform scale + translation (Umeyama)."""
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    if len(src) == 1:
        shift = dst[0] - src[0]
        return lambda pts: np.atleast_2d(np.asarray(pts, dtype=float)) + shift
    mu_s, mu_d = src.mean(axis=0), dst.mean(axis=0)
    S, D = src - mu_s, dst - mu_d
    cov = D.T @ S / len(src)
    U, sig, Vt = np.linalg.svd(cov)
    corr = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        corr[2, 2] = -1.0
    R = U @ corr @ Vt
    var = (S ** 2).sum() / len(src)
    scale = float((sig * np.diag(corr)).sum() / var) if var > 1e-12 else 1.0

    def warp(pts):
        pts = np.atleast_2d(np.asarray(pts, dtype=float))
        return (pts - mu_s) @ (R.T * scale) + mu_d

    return warp


def bbox_landmarks(src_surface, dst_surface):
    """Corners of the two bounding boxes, as a correspondence of last resort.

    Enough to place a layout in roughly the right region at roughly the right
    scale. It knows nothing about anatomy, so it is a starting point to edit,
    not an answer.
    """
    def corners(surf):
        lo = surf.verts.min(axis=0)
        hi = surf.verts.max(axis=0)
        return np.array([[x, y, z] for x in (lo[0], hi[0])
                         for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    return corners(src_surface), corners(dst_surface)


def retarget(graph, dst_surface, src_points, dst_points, smoothing=0.0):
    """Move every node and arc sample onto the target surface. In place.

    Returns a report. The graph's topology — which arcs meet at which nodes,
    which patches they bound — is untouched by construction; only positions and
    pins change, so holes, seams, arc types and locked counts all survive.
    """
    warp = thin_plate_warp(src_points, dst_points, smoothing)

    node_ids = sorted(graph.nodes)
    if node_ids:
        moved = warp(np.array([graph.nodes[n].co for n in node_ids]))
        projected = dst_surface.project(moved)
        for nid, co in zip(node_ids, projected):
            graph.nodes[nid].co = np.asarray(co, dtype=float)
            graph.nodes[nid].pin = dst_surface.pin(co)

    drift = []
    for arc in graph.arcs.values():
        path = np.asarray(arc.path, dtype=float)
        if len(path) < 2:
            continue
        new = dst_surface.project(warp(path))
        new[0] = graph.nodes[arc.a].co
        new[-1] = graph.nodes[arc.b].co
        arc.path = new
        arc.pins = [dst_surface.pin(p) for p in new]
        drift.append(float(np.linalg.norm(new - path, axis=1).mean()))

    return {
        "landmarks": len(src_points),
        "nodes": len(node_ids),
        "arcs": len(graph.arcs),
        "patches": len(graph.patches),
        "mean_drift": float(np.mean(drift)) if drift else 0.0,
    }
