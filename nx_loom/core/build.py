"""Graph -> mesh. The rebuild pipeline of SPEC §4.

Arc vertices are instantiated exactly once and both neighbouring patches index
into them, so patches are welded by construction — there is no distance merge
anywhere in this file, and there must never be one.
"""

from __future__ import annotations

import numpy as np

from .fill import fill_patch
from .quantize import quantize
from .surface import resample


BACKGROUND_MIN_RATIO = 6.0
BACKGROUND_MIN_SHARE = 0.5


def background_patches(graph):
    """Patches that are 'the rest of the model' rather than a region you drew.

    A closed loop drawn around a limb splits a closed surface into two valid
    regions: the limb, and everything else. Both are real patches, so filling
    them all sprays geometry over the entire hull. A region that dwarfs every
    other one was almost certainly not the thing being worked on, so it is left
    alone and reported instead of filled.
    """
    if len(graph.patches) < 2:
        return set()
    areas = {pid: graph.patch_area(pid) for pid in graph.patches}
    values = [a for a in areas.values() if a > 0]
    if not values:
        return set()
    smallest, total = min(values), sum(values)
    if smallest <= 0 or total <= 0:
        return set()
    return {pid for pid, a in areas.items()
            if a > smallest * BACKGROUND_MIN_RATIO and a > total * BACKGROUND_MIN_SHARE}


def build(graph, target_edge=None, project=None, relax_iters=20,
          fill_background=False):
    """Returns (verts (N,3), quads, provenance, report).

    ``provenance[i]`` says where vertex i came from — a node, a point along an
    arc, or a parameterised point inside a patch. It is what the delta layer
    keys hand edits on, so an edit survives a rebuild instead of being keyed to
    a vertex index that means something different next time.
    """
    target_edge = target_edge or graph.settings.get("target_edge", 0.1)
    arc_ids = list(graph.arcs)
    lengths = {a: graph.arcs[a].length() for a in arc_ids}
    locks = {a: graph.arcs[a].n_lock for a in arc_ids if graph.arcs[a].n_lock}

    counts, qrep = quantize(
        arc_ids, lengths, target_edge,
        list(graph.patches), lambda p: graph.patches[p].arc_sides(), locks,
    )
    for a in arc_ids:
        graph.arcs[a].n = counts[a]

    verts = []
    prov = []
    node_vert = {}

    def add(pt, tag):
        prov.append(tag)
        return _add(pt)

    def _add(pt):
        verts.append(np.asarray(pt, dtype=float))
        return len(verts) - 1

    for nid, node in graph.nodes.items():
        node_vert[nid] = add(node.co, ("n", int(nid)))

    # arc vertices: endpoints are the shared node vertices, interior is new
    arc_verts = {}
    for aid in arc_ids:
        arc = graph.arcs[aid]
        pts = resample(arc.path, counts[aid], project=project)
        ids = [node_vert[arc.a]]
        for k in range(1, counts[aid]):
            ids.append(add(pts[k], ("a", int(aid), k / counts[aid])))
        ids.append(node_vert[arc.b])
        verts[ids[0]] = np.asarray(graph.nodes[arc.a].co, dtype=float)
        verts[ids[-1]] = np.asarray(graph.nodes[arc.b].co, dtype=float)
        arc_verts[aid] = ids

    quads = []
    quad_patch = []
    failed = []
    holes = []
    background = set() if fill_background else background_patches(graph)
    for pid, patch in graph.patches.items():
        if patch.fill == "hole":
            holes.append(pid)
            continue
        if pid in background:
            failed.append((pid, "background"))
            continue
        if pid in qrep["unsatisfied_patches"]:
            failed.append((pid, "unquantized"))
            continue

        side_ids, side_pts = [], []
        for side in patch.sides:
            ids = []
            for aid, reversed_ in side:
                seq = arc_verts[aid][::-1] if reversed_ else arc_verts[aid]
                ids.extend(seq if not ids else seq[1:])
            side_ids.append(ids)
            side_pts.append([verts[i] for i in ids])

        res = fill_patch(side_pts, relax_iters=relax_iters, project=project)
        if res is None:
            failed.append((pid, "no valid split"))
            continue
        loc_verts, loc_quads, slots, params = res

        remap = {}
        for (_, si, k), loc in slots.items():
            remap[loc] = side_ids[si][k]
        for loc in range(len(loc_verts)):
            if loc not in remap:
                if params is not None and loc in params:
                    u, v = params[loc]
                    tag = ("p", int(pid), float(u), float(v))
                else:
                    tag = ("q", int(pid), int(loc))
                remap[loc] = add(loc_verts[loc], tag)
        patch_quads = [tuple(remap[i] for i in q) for q in loc_quads]
        if _would_be_nonmanifold(quads, patch_quads):
            # A patch whose fill collides with an already-placed one means the
            # layout was mis-traversed. Emitting it would hand the artist a
            # broken mesh that looks fine until they subdivide; refusing it and
            # naming the patch is the honest failure.
            failed.append((pid, "non-manifold"))
            continue
        quads.extend(patch_quads)
        quad_patch.extend([pid] * len(patch_quads))

    used = {i for q in quads for i in q}
    keep = sorted(used)
    compact = {old: new for new, old in enumerate(keep)}
    out_verts = np.array([verts[i] for i in keep]) if keep else np.zeros((0, 3))
    out_quads = [tuple(compact[i] for i in q) for q in quads]
    out_prov = [prov[i] for i in keep]

    report = dict(qrep)
    report.update({
        "verts": len(out_verts),
        "quads": len(out_quads),
        "failed_patches": failed,
        "holes": holes,
        "background": sorted(background),
        "quad_patch": quad_patch,
        "dropped_verts": len(verts) - len(out_verts),
        "target_edge": target_edge,
    })
    return out_verts, out_quads, out_prov, report


def _would_be_nonmanifold(existing, incoming):
    cnt = {}
    for q in list(existing) + list(incoming):
        for k in range(4):
            e = (q[k], q[(k + 1) % 4])
            e = e if e[0] < e[1] else (e[1], e[0])
            cnt[e] = cnt.get(e, 0) + 1
            if cnt[e] > 2:
                return True
    return False


def mesh_stats(verts, quads):
    """Cheap structural sanity: shared-edge counts and non-manifold detection."""
    cnt = {}
    for q in quads:
        for k in range(4):
            e = (q[k], q[(k + 1) % 4])
            e = e if e[0] < e[1] else (e[1], e[0])
            cnt[e] = cnt.get(e, 0) + 1
    return {
        "verts": len(verts),
        "quads": len(quads),
        "edges": len(cnt),
        "boundary_edges": sum(1 for c in cnt.values() if c == 1),
        "nonmanifold_edges": sum(1 for c in cnt.values() if c > 2),
        "euler": len(verts) - len(cnt) + len(quads),
    }
