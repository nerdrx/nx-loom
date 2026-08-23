"""Unpaired arcs under symmetry: detection and repair.

A layout that LOOKS mirrored can be two independent halves — both sides drawn
by hand, too different to pair. Then each side quantises on its own, and one
side of a "mirrored" layout fails to solve while the other is fine, which is
exactly how it was reported. The tool must show the unpaired region and offer
a way back to a truly mirrored document.
"""

import bpy
import numpy as np

from nx_loom.core import authoring as A
from nx_loom.core.surface import Surface
from nx_loom.core.symmetry import unpaired_arcs
from nx_loom.ops.layout import get_graph, rebuild_object, set_graph


def _setup():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=1.0)
    src = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.22
    st.relax_iters = 2
    st.symmetry_axis = "X"
    bpy.ops.nxloom.new_layout()
    return src, bpy.context.active_object, \
        Surface(src, bpy.context.evaluated_depsgraph_get())


def _draw_both_sides(g, surf, skew=0.03, extra_diagonal=True):
    def P(x, y, z):
        v = np.array([x, y, z], float)
        return v / np.linalg.norm(v)

    def seg(a, b, n=10):
        a, b = P(*a), P(*b)
        om = np.arccos(np.clip(a @ b, -1, 1))
        return np.array([(np.sin((1 - t) * om) * a + np.sin(t * om) * b)
                         / np.sin(om) for t in [k / n for k in range(n + 1)]])

    def node_at(p):
        hit = A.nearest_node(g, P(*p), 0.05)
        return hit[0] if hit else A.new_node(g, P(*p), surf)

    def arc(p, q):
        A.add_arc(g, node_at(p), node_at(q), seg(p, q), surf)

    xs = [0.25, 0.6, 0.95]
    ys = [-0.45, 0.0, 0.45]
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            if i < 2:
                arc((x, y, 0.6), (xs[i + 1], y, 0.6))
            if j < 2:
                arc((x, y, 0.6), (x, ys[j + 1], 0.6))
    if skew == 0.0 and not extra_diagonal:
        # the paired scenario: an exact hand-drawn mirror of the same block
        for i, x in enumerate(xs):
            for j, y in enumerate(ys):
                if i < 2:
                    arc((-x, y, 0.6), (-xs[i + 1], y, 0.6))
                if j < 2:
                    arc((-x, y, 0.6), (-x, ys[j + 1], 0.6))
    else:
        # the reported scenario: the other side has genuinely DIFFERENT
        # topology — a coarser block plus a diagonal, like the shoulder that
        # failed one-sided. Nothing here can pair with the fine side.
        arc((-0.25, -0.45, 0.6), (-0.95, -0.45, 0.6))
        arc((-0.25, 0.45, 0.6), (-0.95, 0.45, 0.6))
        arc((-0.25, -0.45, 0.6), (-0.25, 0.45, 0.6))
        arc((-0.95, -0.45, 0.6), (-0.95, 0.45, 0.6))
        arc((-0.25, -0.45, 0.6), (-0.95, 0.45, 0.6))


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    # -- scenario 1 (own scene): exactly mirrored hand-drawn sides pair
    src2, obj2, surf2 = _setup()
    g2 = get_graph(obj2)
    _draw_both_sides(g2, surf2, skew=0.0, extra_diagonal=False)
    set_graph(obj2, g2)
    rebuild_object(obj2, bpy.context)
    g2 = get_graph(obj2)
    st = bpy.context.scene.nx_loom
    out.append(("exactly mirrored hand-drawn sides pair cleanly",
                len(unpaired_arcs(g2, "X", st.symmetry_tolerance)) == 0,
                f"{len(unpaired_arcs(g2, 'X', st.symmetry_tolerance))}"))

    # the invariant behind the user's question: a PAIRED region cannot fail
    # one-sided — partners share one count and fail together
    from nx_loom.core.build import _solve_counts
    counts, qrep = _solve_counts(g2, 0.22)
    twins = [(a, arc.twin) for a, arc in g2.arcs.items() if arc.twin is not None]
    same = all(counts[a] == counts[t] for a, t in twins)
    out.append(("paired arcs always carry identical counts",
                same and len(twins) >= 6, f"{len(twins)} pairs checked"))

    # -- scenario 2 (fresh scene): mismatched sides, detection, then repair
    src, obj, surf = _setup()
    st = bpy.context.scene.nx_loom
    g = get_graph(obj)
    _draw_both_sides(g, surf)
    set_graph(obj, g)
    rebuild_object(obj, bpy.context)
    g = get_graph(obj)

    loose = unpaired_arcs(g, "X", st.symmetry_tolerance)
    out.append(("structurally different sides are detected as unpaired",
                len(loose) >= 10, f"{len(loose)} unpaired"))
    sides = {("pos" if np.asarray(g.arcs[a].path)[:, 0].mean() > 0 else "neg")
             for a in loose}
    out.append(("both independent halves are flagged", sides == {"pos", "neg"},
                str(sides)))

    bpy.context.view_layer.objects.active = obj
    res = bpy.ops.nxloom.symmetrize_side(keep="POS")
    out.append(("symmetrize finishes", "FINISHED" in res, str(res)))
    g = get_graph(obj)
    left = unpaired_arcs(g, "X", st.symmetry_tolerance)
    out.append(("afterwards nothing is unpaired", len(left) == 0, str(len(left))))
    n_auth_pos = sum(1 for a in g.arcs.values()
                     if a.mirror_of is None
                     and np.asarray(a.path)[:, 0].mean() > st.symmetry_tolerance)
    n_mir = sum(1 for a in g.arcs.values() if a.mirror_of is not None)
    out.append(("the kept side is now truly mirrored",
                n_mir == n_auth_pos and n_mir >= 10,
                f"{n_auth_pos} authored+ vs {n_mir} mirrored"))

    P = np.array([tuple(obj.matrix_world @ v.co) for v in obj.data.vertices])
    if len(P):
        M = P.copy()
        M[:, 0] *= -1
        d = np.linalg.norm(P[:, None, :] - M[None, :, :], axis=2).min(axis=1)
        out.append(("and the mesh is exactly symmetric again",
                    float(d.max()) < 1e-9, f"max {d.max():.1e}"))

    out.append(("no unpaired arcs -> the repair declines politely",
                "CANCELLED" in bpy.ops.nxloom.symmetrize_side(
                    keep="POS", scope="LOOSE"), ""))

    out += run_mirrored_discovery()
    return out


def run_mirrored_discovery():
    """Mirrored-side patches are constructed, not rediscovered.

    Discovery uses surface normals from the reference and a corner-angle
    threshold, and the sculpt's triangulation is not symmetric — a borderline
    corner call can flip on one side only, giving exactly-mirrored regions
    DIFFERENT patch structures. That is how one cheek of a "27 mirrored, 0
    twinned" layout failed to solve while the other was fine.
    """
    from nx_loom.core.build import _solve_counts
    from nx_loom.core.symmetry import enforce_mirrored_patches

    out = []
    src, obj, surf = _setup()
    st = bpy.context.scene.nx_loom
    g = get_graph(obj)
    _draw_both_sides(g, surf, skew=0.0, extra_diagonal=False)
    # authored on + side only for this check: wipe the hand-drawn - side so
    # sync derives it
    for aid in [a for a, arc in g.arcs.items()
                if np.asarray(arc.path)[:, 0].mean() < -st.symmetry_tolerance]:
        del g.arcs[aid]
    from nx_loom.core.authoring import prune_orphan_nodes
    prune_orphan_nodes(g)
    set_graph(obj, g)
    rebuild_object(obj, bpy.context)
    g = get_graph(obj)

    derived = {a for a, arc in g.arcs.items() if arc.mirror_of is not None}
    out.append(("the negative side is fully derived", len(derived) >= 10,
                f"{len(derived)} derived"))

    def structure(pid):
        return tuple(sorted(len(side) for side in g.patches[pid].sides))

    def is_derived_patch(pid):
        arcs = {a for side in g.patches[pid].arc_sides() for a in side}
        off = [a for a in arcs
               if abs(np.asarray(g.arcs[a].path)[:, 0]).max()
               > st.symmetry_tolerance]
        return bool(off) and all(a in derived for a in off)

    derived_pids = [p for p in g.patches if is_derived_patch(p)]
    authored_pids = [p for p in g.patches if not is_derived_patch(p)]
    out.append(("mirrored-side patches exist", len(derived_pids) >= 4,
                f"{len(derived_pids)} of {len(g.patches)}"))
    canon_a = {g.canonical_key(p) for p in authored_pids}
    matched = sum(1 for p in derived_pids if g.canonical_key(p) in canon_a)
    out.append(("every mirrored patch shares its source's canonical key",
                matched == len(derived_pids),
                f"{matched}/{len(derived_pids)}"))

    # corrupt the mirrored decomposition, then let enforcement restore it —
    # this is the borderline-corner flip, staged deterministically
    n_before = len(g.patches)
    victim = derived_pids[0]
    del g.patches[victim]
    made = enforce_mirrored_patches(g, "X", st.symmetry_tolerance)
    restored = [p for p in g.patches if is_derived_patch(p)]
    out.append(("a corrupted mirrored decomposition is reconstructed",
                made >= 1 and len(g.patches) == n_before
                and len(restored) == len(derived_pids),
                f"rebuilt {made}, {len(g.patches)} patches"))

    # the invariant that was violated on the report: unsolvable regions go red
    # on BOTH sides. Conflicting pins on one authored patch's opposite sides
    # make it genuinely unsolvable; its mirror must fail with it.
    pid = authored_pids[0]
    sides = g.patches[pid].arc_sides()
    if len(sides) == 4 and len(sides[0]) == 1 and len(sides[2]) == 1:
        g.arcs[sides[0][0]].n_lock = 3
        g.arcs[sides[2][0]].n_lock = 9
        set_graph(obj, g)
        rep = rebuild_object(obj, bpy.context)
        g = get_graph(obj)
        bad = set(rep["unsatisfied_patches"])
        bad_keys = {g.canonical_key(p) for p in bad if p in g.patches}
        pairs_complete = all(
            sum(1 for q in g.patches if g.canonical_key(q) == k) ==
            sum(1 for q in bad if q in g.patches
                and g.canonical_key(q) == k)
            for k in bad_keys)
        out.append(("an unsolvable region now fails on BOTH sides",
                    len(bad) >= 2 and pairs_complete,
                    f"{len(bad)} bad patches across {len(bad_keys)} region(s)"))
    return out
