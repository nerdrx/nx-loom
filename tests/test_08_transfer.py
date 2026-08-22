"""Apply carries the reference's data onto the new topology.

This is the difference between a topology toy and something you can point at a
rigged, UV'd, shape-keyed avatar. The projection itself is QuadForge's
(vendored, see core/vendor/PROVENANCE.md) — what is tested here is that NX Loom
hands it the right two meshes and does not lose the result.
"""

import bmesh
import bpy
import numpy as np


def _rich_sphere():
    """A reference with every kind of data Apply is supposed to preserve."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=1.0)
    src = bpy.context.active_object
    me = src.data

    # two materials, split by hemisphere
    for name in ("TopMat", "BotMat"):
        me.materials.append(bpy.data.materials.new(name))
    for poly in me.polygons:
        poly.material_index = 0 if poly.center.z >= 0 else 1

    # a vertex group weighted by height
    vg = src.vertex_groups.new(name="Spine")
    for v in me.vertices:
        vg.add([v.index], float(min(max((v.co.z + 1.0) * 0.5, 0.0), 1.0)), "REPLACE")

    # a basis plus one displaced shape key
    src.shape_key_add(name="Basis", from_mix=False)
    key = src.shape_key_add(name="Bulge", from_mix=False)
    for i, v in enumerate(me.vertices):
        key.data[i].co = v.co * (1.35 if v.co.x > 0 else 1.0)

    # a crease on a ring of edges
    bm = bmesh.new()
    bm.from_mesh(me)
    layer = bm.edges.layers.float.new("crease_edge") if \
        "crease_edge" not in bm.edges.layers.float else \
        bm.edges.layers.float["crease_edge"]
    n_creased = 0
    for e in bm.edges:
        if abs(e.verts[0].co.z) < 0.05 and abs(e.verts[1].co.z) < 0.05:
            e[layer] = 1.0
            n_creased += 1
    bm.to_mesh(me)
    bm.free()
    return src, n_creased


def _trace_layout(src, target_edge=0.3, transfer=True):
    st = bpy.context.scene.nx_loom
    st.target_edge = target_edge
    st.relax_iters = 8
    st.transfer_data = transfer
    bpy.context.view_layer.objects.active = src
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    res = bpy.ops.nxloom.layout_from_selection()
    if bpy.context.active_object.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
    return bpy.context.active_object if "FINISHED" in res else None


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    from nx_loom.core.vendor import qf_transfer
    out.append(("vendored transfer imports",
                hasattr(qf_transfer, "capture") and hasattr(qf_transfer, "apply"), ""))

    src, n_creased = _rich_sphere()
    obj = _trace_layout(src)
    if obj is None:
        return out + [("layout traced", False, "operator cancelled")]
    out.append(("layout traced", len(obj.data.polygons) > 0,
                f"{len(obj.data.polygons)} faces"))

    before = len(obj.data.polygons)
    res = bpy.ops.nxloom.apply()
    out.append(("apply finished", "FINISHED" in res, str(res)))
    me = obj.data

    out.append(("topology untouched by transfer", len(me.polygons) == before,
                f"{before} -> {len(me.polygons)}"))
    out.append(("still all quads",
                all(len(p.vertices) == 4 for p in me.polygons), ""))

    # UVs
    ok_uv = len(me.uv_layers) > 0
    spread = 0.0
    if ok_uv:
        uv = np.array([tuple(d.uv) for d in me.uv_layers[0].data])
        spread = float(uv.max() - uv.min())
    out.append(("UVs transferred", ok_uv and spread > 0.2,
                f"{len(me.uv_layers)} layer(s), spread {spread:.3f}"))

    # vertex groups
    vg = obj.vertex_groups.get("Spine")
    weights = []
    if vg is not None:
        for v in me.vertices:
            for g in v.groups:
                if g.group == vg.index:
                    weights.append(g.weight)
    ok_w = vg is not None and len(weights) > len(me.vertices) * 0.4
    corr = 0.0
    if ok_w:
        # the group was height-weighted; it must still track height
        zs, ws = [], []
        for v in me.vertices:
            for g in v.groups:
                if g.group == vg.index:
                    zs.append(v.co.z)
                    ws.append(g.weight)
        corr = float(np.corrcoef(zs, ws)[0, 1]) if len(zs) > 3 else 0.0
    out.append(("vertex weights transferred", ok_w, f"{len(weights)} weighted verts"))
    out.append(("weights still track the source gradient", corr > 0.95,
                f"corr {corr:.4f}"))

    # shape keys
    keys = me.shape_keys.key_blocks if me.shape_keys else []
    names = [k.name for k in keys]
    moved = 0.0
    if "Bulge" in names:
        basis = np.array([tuple(k.co) for k in keys["Basis"].data])
        bulge = np.array([tuple(k.co) for k in keys["Bulge"].data])
        moved = float(np.linalg.norm(bulge - basis, axis=1).max())
    out.append(("shape keys transferred", "Bulge" in names and "Basis" in names,
                str(names)))
    out.append(("shape key still displaces geometry", moved > 0.2,
                f"max {moved:.3f}"))

    # materials
    slots = [m.name for m in me.materials]
    used = {p.material_index for p in me.polygons}
    out.append(("material slots transferred", slots == ["TopMat", "BotMat"], str(slots)))
    out.append(("both materials still assigned", used == {0, 1}, str(sorted(used))))

    # the layout is gone; this is a plain mesh now
    out.append(("layout dropped on apply",
                "nx_loom_graph" not in obj and "nx_loom_delta" not in obj, ""))

    # transfer can be turned off
    src2, _ = _rich_sphere()
    obj2 = _trace_layout(src2, transfer=False)
    if obj2 is not None:
        bpy.ops.nxloom.apply()
        out.append(("transfer is opt-out", len(obj2.data.uv_layers) == 0,
                    f"{len(obj2.data.uv_layers)} uv layers"))
    return out
