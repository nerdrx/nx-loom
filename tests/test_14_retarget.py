"""Moving a layout from one mesh onto another.

The claim is that a layout is *intent*, and intent transfers where vertices
cannot. What must hold: the topology survives exactly — same arcs, same
patches, same holes, same seams — and the result sits on the new surface.
"""

import bmesh
import bpy
import numpy as np

from nx_loom.core import retarget as rt
from nx_loom.core.surface import Surface
from nx_loom.ops.layout import get_graph, set_graph
from nx_loom.ops.retarget import bone_landmarks, empty_landmarks


def _sphere(name, radius=1.0, loc=(0, 0, 0), scale=(1, 1, 1)):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12,
                                         radius=radius, location=loc)
    o = bpy.context.active_object
    o.name = name
    o.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return o


def _layout_on(src, target_edge=0.35):
    st = bpy.context.scene.nx_loom
    st.target_edge = target_edge
    st.relax_iters = 2
    st.size_mode = "EDGE"
    st.symmetry_axis = "NONE"
    bpy.context.view_layer.objects.active = src
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.nxloom.layout_from_selection()
    if bpy.context.active_object.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
    return bpy.context.active_object


def _survey(o):
    bm = bmesh.new()
    bm.from_mesh(o.data)
    d = dict(V=len(bm.verts), F=len(bm.faces),
             nm=sum(1 for e in bm.edges if len(e.link_faces) > 2),
             nonquad=sum(1 for f in bm.faces if len(f.verts) != 4),
             bnd=sum(1 for e in bm.edges if len(e.link_faces) == 1))
    bm.free()
    return d


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # the warp itself
    rng = np.random.default_rng(0)
    src = rng.normal(size=(8, 3))
    dst = src * 1.7 + np.array([1.0, 2.0, 3.0])
    w = rt.thin_plate_warp(src, dst)
    out.append(("the warp reproduces a similarity exactly",
                float(np.abs(w(src) - dst).max()) < 1e-8, ""))
    bent = dst.copy()
    bent[3] += np.array([0.5, 0.0, 0.0])
    w2 = rt.thin_plate_warp(src, bent)
    out.append(("and interpolates every landmark when it is not one",
                float(np.abs(w2(src) - bent).max()) < 1e-6, ""))
    w3 = rt.thin_plate_warp(src[:3], dst[:3])
    out.append(("under four landmarks it falls back to a similarity fit",
                float(np.abs(w3(src[:3]) - dst[:3]).max()) < 1e-8, ""))

    # a real retarget: sphere -> squashed, offset sphere
    bpy.ops.wm.read_factory_settings(use_empty=True)
    a = _sphere("A")
    b = _sphere("B", radius=1.0, loc=(4, 0, 0), scale=(1.0, 1.0, 0.6))
    obj = _layout_on(a)
    graph = get_graph(obj)
    before = (len(graph.nodes), len(graph.arcs), len(graph.patches))
    sides_before = sorted(len(p.sides) for p in graph.patches.values())

    # mark a hole and a seam: both must survive the move
    pid = sorted(graph.patches)[0]
    graph.set_hole(pid, True)
    seam_arc = sorted(graph.arcs)[0]
    graph.arcs[seam_arc].type = "seam"
    set_graph(obj, graph)

    bpy.context.scene.nx_loom.retarget_to = b
    res = bpy.ops.nxloom.retarget(method="BOUNDS")
    out.append(("retarget finished", "FINISHED" in res, str(res)))

    graph = get_graph(obj)
    after = (len(graph.nodes), len(graph.arcs), len(graph.patches))
    out.append(("the topology is unchanged", before == after,
                f"{before} -> {after}"))
    out.append(("patch arities are unchanged",
                sorted(len(p.sides) for p in graph.patches.values())
                == sides_before, ""))
    out.append(("the reference now points at the target",
                graph.reference == b.name, graph.reference))
    out.append(("holes survive the move",
                sum(1 for p in graph.patches.values() if p.fill == "hole") == 1,
                ""))
    out.append(("arc types survive the move",
                sum(1 for x in graph.arcs.values() if x.type == "seam") >= 1, ""))
    out.append(("every node is re-pinned to the target",
                all(n.pin is not None for n in graph.nodes.values()), ""))

    # geometry must land on the target surface, not float between the two
    surf_b = Surface(b, bpy.context.evaluated_depsgraph_get())
    P = np.array([tuple(obj.matrix_world @ v.co) for v in obj.data.vertices])
    dev = np.linalg.norm(P - surf_b.project(P), axis=1)
    diag = float(max(b.dimensions))
    out.append(("the rebuilt mesh sits on the target surface",
                len(P) > 0 and float(dev.max()) < diag * 0.05,
                f"max deviation {dev.max():.4f} of {diag:.2f}"))
    near_a = np.linalg.norm(P - np.array([0.0, 0.0, 0.0]), axis=1).min()
    out.append(("and not on the one it came from", near_a > 1.5,
                f"closest approach to the source {near_a:.2f}"))
    st = _survey(obj)
    # one patch was deliberately holed before the move, so its rim is the only
    # boundary the mesh should have
    out.append(("and is still clean, with only the hole's rim open",
                st["nm"] == 0 and st["nonquad"] == 0 and 0 < st["bnd"] <= 32,
                str(st)))

    # bone landmarks: two rigs sharing names are a free correspondence
    bpy.ops.wm.read_factory_settings(use_empty=True)
    a = _sphere("A")
    b = _sphere("B", loc=(4, 0, 0))
    rigs = []
    for host, offset in ((a, 0.0), (b, 4.0)):
        bpy.ops.object.armature_add(location=(offset, 0, -1))
        arm = bpy.context.active_object
        bpy.ops.object.mode_set(mode="EDIT")
        eb = arm.data.edit_bones[0]
        eb.name = "Hips"
        for nm, head, tail in (("Spine", (0, 0, 1), (0, 0, 2)),
                               ("Head", (0, 0, 2), (0, 0, 3))):
            nb = arm.data.edit_bones.new(nm)
            nb.head, nb.tail = head, tail
        bpy.ops.object.mode_set(mode="OBJECT")
        host.modifiers.new("Armature", "ARMATURE").object = arm
        rigs.append(arm)
    pairs = bone_landmarks(a, b)
    out.append(("shared bone names become landmarks",
                pairs is not None and len(pairs[0]) >= 3,
                f"{0 if pairs is None else len(pairs[0])} pairs"))
    if pairs is not None:
        shift = (pairs[1] - pairs[0])[:, 0]
        out.append(("and they describe the right displacement",
                    float(np.abs(shift - 4.0).max()) < 1e-4,
                    f"x shift {shift.min():.2f}..{shift.max():.2f}"))

    out.append(("no landmarks at all is reported, not guessed",
                empty_landmarks(a, b) is None, ""))
    return out
