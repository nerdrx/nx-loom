"""One sweep chunk: all densities of ONE primitive, in one Blender process.

Driven by scripts/sweep.sh — a fresh process per primitive keeps memory flat
(the old single-process sweep accumulated fill/count/surface caches across
117 layouts and could fill RAM), and module caches are cleared between
layouts so nothing stale survives Blender's pointer reuse either.

Env: NXL_PRIM = primitive key. Prints one line per layout and a final
"CHUNK <prim> <n> <bad>" line — the driver sums them; no line, no proof.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bpy  # noqa: E402
import nx_loom  # noqa: E402

nx_loom.register()

PRIMS = {
    "uv_sphere_12_6": (lambda: bpy.ops.mesh.primitive_uv_sphere_add(
        segments=12, ring_count=6), 3),
    "uv_sphere_16_8": (lambda: bpy.ops.mesh.primitive_uv_sphere_add(
        segments=16, ring_count=8), 3),
    "uv_sphere_24_12": (lambda: bpy.ops.mesh.primitive_uv_sphere_add(
        segments=24, ring_count=12), 3),
    "ico_1": (lambda: bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1), 3),
    "ico_2": (lambda: bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2), 3),
    "ico_3": (lambda: bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3), 3),
    "cyl_8": (lambda: bpy.ops.mesh.primitive_cylinder_add(vertices=8), 3),
    "cyl_16": (lambda: bpy.ops.mesh.primitive_cylinder_add(vertices=16), 3),
    "cyl_24": (lambda: bpy.ops.mesh.primitive_cylinder_add(vertices=24), 3),
    "cone_8": (lambda: bpy.ops.mesh.primitive_cone_add(vertices=8), 2),
    "cone_16": (lambda: bpy.ops.mesh.primitive_cone_add(vertices=16), 2),
    "cone_24": (lambda: bpy.ops.mesh.primitive_cone_add(vertices=24), 2),
    "torus_12_6": (lambda: bpy.ops.mesh.primitive_torus_add(
        major_segments=12, minor_segments=6), 2),
    "torus_16_8": (lambda: bpy.ops.mesh.primitive_torus_add(
        major_segments=16, minor_segments=8), 2),
    "torus_24_12": (lambda: bpy.ops.mesh.primitive_torus_add(
        major_segments=24, minor_segments=12), 2),
}
EDGES = [0.35, 0.22, 0.14]


def clear_caches():
    from nx_loom.core import build as B
    from nx_loom.core import surface as S
    from nx_loom.ops import draw as D
    from nx_loom.ops import layout as L
    B._FILL_CACHE.clear()
    B._COUNT_CACHE.clear()
    S._CACHE.clear()
    L._PEEK.clear()
    D._SEAM_CACHE.clear()


def main():
    prim = os.environ["NXL_PRIM"]
    make, reps = PRIMS[prim]
    total = bad_total = 0
    for edge in EDGES:
        for rep in range(reps):
            clear_caches()
            bpy.ops.wm.read_factory_settings(use_empty=True)
            make()
            st = bpy.context.scene.nx_loom
            st.target_edge = edge * (1.0 + 0.15 * rep)
            st.relax_iters = 2
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            total += 1
            try:
                bpy.ops.nxloom.layout_from_selection()
            except RuntimeError as exc:
                print(f"  FAIL {prim} edge={st.target_edge:.3f}: {exc}")
                bad_total += 1
                continue
            obj = bpy.context.active_object
            from nx_loom.ops.layout import rebuild_object
            rep_r = rebuild_object(obj, bpy.context)
            n_bad = len(list(obj.get("nx_loom_bad_patches", []) or []))
            if rep_r:
                n_bad += len(rep_r.get("unsatisfied_patches", []))
                n_bad += sum(1 for _p, why in rep_r.get("failed_patches", [])
                             if why != "background")
            else:
                n_bad += 1
            if n_bad:
                print(f"  FAIL {prim} edge={st.target_edge:.3f}: "
                      f"{n_bad} broken")
                bad_total += 1
    print(f"CHUNK {prim} {total} {bad_total}", flush=True)


main()
