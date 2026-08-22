"""The delta layer: hand edits that survive a rebuild.

The load-bearing claim is that re-applying at the same density is *lossless*.
If it is not, the non-destructive promise is hollow — the artist's work is
silently degraded every time they touch the density slider.
"""

import bmesh
import bpy
import numpy as np

# Blender stores vertex coordinates as float32, so a value that went through
# mesh storage comes back ~3e-8 off a float64 rebuild. Comparisons against a
# freshly computed clean build have to sit above that noise floor; comparisons
# between two values that both went through the mesh can be exact.
F32_NOISE = 1e-6

from nx_loom.core import delta as delta_mod
from nx_loom.core.graph import GRAPH_KEY
from nx_loom.ops.layout import DELTA_KEY, clean_build, rebuild_object


def _setup(target_edge=0.35):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=2, y_subdivisions=2, size=2.0)
    st = bpy.context.scene.nx_loom
    st.target_edge = target_edge
    st.relax_iters = 0
    st.reproject = False
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.nxloom.layout_from_selection()
    if bpy.context.active_object.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
    return bpy.context.active_object


def _world(obj):
    mw = obj.matrix_world
    return np.array([tuple(mw @ v.co) for v in obj.data.vertices], dtype=float)


def _nudge(obj, count=5, amount=0.25):
    """Move some interior vertices off the generated surface, as a hand edit."""
    mw_inv = obj.matrix_world.inverted()
    mw = obj.matrix_world
    picked = []
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    interior = [v.index for v in bm.verts
                if all(len(e.link_faces) == 2 for e in v.link_edges)]
    bm.free()
    for k, vi in enumerate(interior[:count]):
        co = mw @ obj.data.vertices[vi].co
        co.z += amount * (1 + k * 0.3)
        obj.data.vertices[vi].co = mw_inv @ co
        picked.append(vi)
    return picked


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # -- frames must be a pure function of the normal, or capture/apply disagree
    a = delta_mod.frame((0, 0, 1))
    b = delta_mod.frame((0, 0, 1))
    out.append(("frame is deterministic",
                all(np.allclose(x, y) for x, y in zip(a, b)), ""))
    out.append(("frame is orthonormal",
                abs(a[0] @ a[1]) < 1e-12 and abs(a[0] @ a[2]) < 1e-12
                and abs(np.linalg.norm(a[0]) - 1) < 1e-12, ""))

    # -- capture / same-density rebuild must be lossless
    obj = _setup()
    picked = _nudge(obj)
    edited = _world(obj)
    res = bpy.ops.nxloom.capture_edits()
    out.append(("capture succeeds", "FINISHED" in res, str(res)))
    out.append(("deltas stored on the object", DELTA_KEY in obj, ""))
    stored = delta_mod.load(obj)
    out.append(("one delta per moved vertex",
                delta_mod.count(stored) == len(picked),
                f"{delta_mod.count(stored)} vs {len(picked)}"))
    out.append(("capture records the resolution it was made at",
                bool(stored["dims"]), f"{len(stored['dims'])} owners"))

    rep = rebuild_object(obj, bpy.context)
    after = _world(obj)
    err = float(np.abs(after - edited).max()) if after.shape == edited.shape else 9e9
    out.append(("same-density rebuild is lossless", err < 1e-9, f"max err {err:.2e}"))
    ds = rep.get("delta", {})
    out.append(("all deltas re-applied exactly",
                ds.get("exact") == len(picked) and ds.get("interpolated") == 0
                and ds.get("dropped") == 0, str(ds)))

    # -- untouched vertices must not drift: the failure mode of a global kernel
    clean, prov, _ = clean_build(obj, bpy.context)
    drift = np.linalg.norm(after - clean, axis=1)
    moved = int((drift > F32_NOISE).sum())
    out.append(("only the edited vertices moved", moved == len(picked),
                f"{moved} moved of {len(clean)}, "
                f"unedited max {np.sort(drift)[:-len(picked)].max():.1e}"))

    # -- rebuilding twice must not accumulate
    rebuild_object(obj, bpy.context)
    twice = _world(obj)
    out.append(("repeated rebuilds do not accumulate",
                float(np.abs(twice - edited).max()) < 1e-9, ""))

    # -- edits carry across a density change, with local support
    peak_before = float(np.abs(after - clean)[:, 2].max())
    carried = []
    for te in (0.18, 0.09):
        bpy.context.scene.nx_loom.target_edge = te
        rep2 = rebuild_object(obj, bpy.context)
        c2, p2, _ = clean_build(obj, bpy.context)
        w2 = _world(obj)
        disp = np.linalg.norm(w2 - c2, axis=1)
        carried.append((te, len(c2), int((disp > F32_NOISE).sum()), float(disp.max()),
                        rep2.get("delta", {}).get("dropped", -1)))
    ok_peak = all(abs(c[3] - peak_before) < 0.02 for c in carried)
    ok_local = all(c[2] < len(c2) * 0.5 for c in carried)
    ok_kept = all(c[4] == 0 for c in carried)
    out.append(("edits survive a density change", ok_peak and ok_kept,
                f"peak {peak_before:.3f} -> {[round(c[3], 3) for c in carried]}"))
    out.append(("carried edits stay local, not smeared over the patch", ok_local,
                str([(c[1], c[2]) for c in carried])))

    # -- clearing returns to the pristine surface
    bpy.context.scene.nx_loom.target_edge = 0.35
    rebuild_object(obj, bpy.context)
    bpy.ops.nxloom.clear_edits()
    cleared = _world(obj)
    clean3, _, _ = clean_build(obj, bpy.context)
    out.append(("clear_edits restores the generated surface",
                DELTA_KEY not in obj
                and float(np.abs(cleared - clean3).max()) < F32_NOISE,
                f"max {float(np.abs(cleared - clean3).max()):.1e}"))

    # -- changing the vertex count is a layout change, and must say so
    obj = _setup()
    _nudge(obj, count=2)
    bpy.ops.nxloom.capture_edits()
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.verts[0]], context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()
    # bpy raises RuntimeError when an operator reports ERROR, rather than
    # returning CANCELLED — the refusal is what matters, not the channel.
    refused, msg = False, ""
    try:
        bpy.ops.nxloom.capture_edits()
    except RuntimeError as e:
        refused, msg = True, str(e)
    out.append(("vertex-count change is refused",
                refused and "Vertex count changed" in msg, msg[:70]))
    out.append(("the refusal explains what to do instead",
                "draw it instead" in msg, ""))
    return out
