"""Constrained auto-complete: suggestions respect and complete authored arcs.

Contracts: the field bends to flow into the artist's arcs where they exist
(pinned tangents outweigh curvature), poles buried in authored geometry spawn
nothing (complete the layout, don't redraw it), traces end when they reach an
authored arc so accepting connects them, and a fully authored layout gets
few or no proposals at all.
"""

import bpy
import numpy as np

from nx_loom.core.suggest import (face_frames, guide_pins, smooth_field,
                                  suggest)
from nx_loom.ops.layout import get_graph


def _cyl(R=0.4, n=24, rows=10, h=2.0):
    V, T = [], []
    for r in range(rows + 1):
        z = -h / 2 + h * r / rows
        for i in range(n):
            a = 2 * np.pi * i / n
            V.append([R * np.cos(a), R * np.sin(a), z])
    for r in range(rows):
        for i in range(n):
            j = (i + 1) % n
            a, b = r * n + i, r * n + j
            c, d = (r + 1) * n + i, (r + 1) * n + j
            T.append([a, b, c])
            T.append([b, d, c])
    return np.array(V, float), np.array(T, int)


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # ---- a diagonal guide bends the cylinder's field near it ----------
    V, T = _cyl()
    ts = np.linspace(0.0, 2 * np.pi, 60)
    helix = np.stack([0.4 * np.cos(ts), 0.4 * np.sin(ts),
                      -0.8 + 1.6 * ts / (2 * np.pi)], axis=1)
    frames = face_frames(V, T)
    pin_z, pin_w, soup = guide_pins(V, T, [helix], frames)
    out.append(("guide tangents pin onto nearby faces",
                int((pin_w > 0).sum()) > 20 and len(soup) > 20,
                f"{int((pin_w > 0).sum())} faces pinned"))

    e1, e2, _n = frames
    theta, _f, _p = smooth_field(V, T, pin_z=pin_z, pin_w=pin_w)
    centers = V[T].mean(axis=1)
    aligned = 0
    near = 0
    for f in np.where(pin_w > 0)[0]:
        want = np.angle(pin_z[f]) / 4.0
        d = abs(theta[f] - want) % (np.pi / 2)
        d = min(d, np.pi / 2 - d)
        near += 1
        if d < np.pi / 10:
            aligned += 1
    out.append(("the field flows along the guide where it runs",
                near > 0 and aligned / max(near, 1) > 0.8,
                f"{aligned}/{near} faces within 18 deg"))

    # ---- an equator ring: proposals appear, but never along it --------
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12,
                                         radius=1.0)
    sph = bpy.context.active_object
    sph.data.calc_loop_triangles()
    sv = np.array([tuple(v.co) for v in sph.data.vertices])
    stt = np.array([tuple(t.vertices) for t in sph.data.loop_triangles])
    ring = np.stack([np.cos(ts), np.sin(ts), np.zeros_like(ts)], axis=1)
    polys, _sing = suggest(sv, stt, guides=[ring])
    hugging = 0
    for poly in polys:
        d = np.abs(np.asarray(poly)[:, 2])      # distance to equator plane
        if float((d < 0.08).mean()) > 0.4:
            hugging += 1
    out.append(("proposals exist around an authored ring",
                len(polys) > 0, f"{len(polys)} traces"))
    out.append(("but none of them re-draws the ring",
                hugging == 0, f"{hugging} hugging traces"))

    # ---- a fully authored layout asks for nothing ---------------------
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=6,
                                         radius=1.0)
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.25
    st.relax_iters = 2
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.nxloom.layout_from_selection()
    obj = bpy.context.active_object
    res = bpy.ops.nxloom.suggest_layout()
    graph = get_graph(obj)
    ghosts = graph.settings.get("suggestions") or []
    out.append(("a fully authored layout gets (almost) no proposals",
                "FINISHED" in res and len(ghosts) <= 2,
                f"{len(ghosts)} ghosts"))

    return out
