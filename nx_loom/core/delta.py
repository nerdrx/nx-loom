"""The delta layer: hand edits that survive a rebuild (SPEC §5).

Without this the non-destructive promise is hollow — one nudged vertex and the
next rebuild throws it away. Edits are stored against a vertex's *provenance*
(which node, where along which arc, where inside which patch) rather than its
index, because an index means something different at a different density.

Offsets are held in a local frame derived from the surface normal, so an edit
follows the sculpt if the reference changes instead of hanging in world space.
"""

from __future__ import annotations

import json

import numpy as np

DELTA_KEY = "nx_loom_delta"
SCHEMA = 1


def frame(normal):
    """Deterministic orthonormal frame from a normal alone.

    Determinism is the whole requirement: capture and re-apply must agree, and
    they will if the frame is a pure function of the normal at that point.
    """
    n = np.asarray(normal, dtype=float)
    ln = np.linalg.norm(n)
    n = np.array([0.0, 0.0, 1.0]) if ln < 1e-12 else n / ln
    ref = np.zeros(3)
    ref[int(np.argmin(np.abs(n)))] = 1.0
    t = np.cross(n, ref)
    t /= max(np.linalg.norm(t), 1e-12)
    b = np.cross(n, t)
    return t, b, n


def key_of(tag):
    kind = tag[0]
    if kind == "n":
        return f"n:{tag[1]}"
    if kind == "a":
        return f"a:{tag[1]}:{tag[2]:.6f}"
    if kind == "p":
        return f"p:{tag[1]}:{tag[2]:.6f}:{tag[3]:.6f}"
    return f"q:{tag[1]}:{tag[2]}"


def _normal_of(normals, i, pos):
    """normals may be an array or a callable — the callable form lets callers
    avoid a BVH query for every vertex when only a handful carry an edit."""
    return np.asarray(normals(pos) if callable(normals) else normals[i], dtype=float)


def dims_of(provenance):
    """Capture-time resolution per owner, from the *full* provenance list.

    This cannot be inferred from the stored samples alone. An arc whose only
    edit sits at t=2/3 would reconstruct as n=2, and the offset would then be
    smeared along the whole arc instead of landing on one vertex. Every
    interior sample is present in the provenance, so the true count is the
    smallest parameter step there.
    """
    us, vs, ts = {}, {}, {}
    for tag in provenance:
        if tag[0] == "a":
            ts.setdefault(tag[1], []).append(tag[2])
        elif tag[0] == "p":
            us.setdefault(tag[1], []).append(tag[2])
            vs.setdefault(tag[1], []).append(tag[3])
    dims = {}
    for aid, vals in ts.items():
        lo = min(v for v in vals if v > 1e-9) if any(v > 1e-9 for v in vals) else 0
        if lo:
            dims[f"a:{aid}"] = [int(round(1.0 / lo))]
    for pid in us:
        lu = min(v for v in us[pid] if v > 1e-9) if any(v > 1e-9 for v in us[pid]) else 0
        lv = min(v for v in vs[pid] if v > 1e-9) if any(v > 1e-9 for v in vs[pid]) else 0
        if lu and lv:
            dims[f"p:{pid}"] = [int(round(1.0 / lu)), int(round(1.0 / lv))]
    return dims


def capture(clean, edited, provenance, normals, tol=1e-6):
    """Difference two same-length vertex arrays into a delta table.

    Returns {"offsets": {...}, "dims": {...}} — the dims are the resolution the
    edits were made at, and re-applying at a different one resamples against
    them.
    """
    clean = np.asarray(clean, dtype=float)
    edited = np.asarray(edited, dtype=float)
    if clean.shape != edited.shape:
        raise ValueError(f"vertex count changed: {len(clean)} -> {len(edited)}")
    out = {}
    for i, tag in enumerate(provenance):
        d = edited[i] - clean[i]
        if float(np.linalg.norm(d)) <= tol:
            continue
        t, b, n = frame(_normal_of(normals, i, clean[i]))
        out[key_of(tag)] = [float(d @ t), float(d @ b), float(d @ n)]
    return {"offsets": out, "dims": dims_of(provenance)}


def _parse(key):
    parts = key.split(":")
    kind = parts[0]
    if kind == "n":
        return kind, int(parts[1]), ()
    if kind == "a":
        return kind, int(parts[1]), (float(parts[2]),)
    if kind == "p":
        return kind, int(parts[1]), (float(parts[2]), float(parts[3]))
    return kind, int(parts[1]), (int(parts[2]),)


def _reconstruct(offsets, dims):
    """Rebuild the capture-time displacement grids from the stored samples.

    Interpolating the stored points directly with a distance weighting was
    wrong: that kernel has global support, so re-applying at the *same* density
    displaced every other vertex in an edited patch too. A displacement grid
    that is zero except where the artist actually moved something, sampled
    bilinearly, is exact at grid points by construction and decays to zero away
    from an edit — which is what both cases need.

    The grid resolution comes from the recorded dims, not from the samples.
    """
    raw = {}
    for key, off in offsets.items():
        kind, oid, params = _parse(key)
        if kind in ("a", "p"):
            raw.setdefault((kind, oid), []).append((params, np.asarray(off, float)))

    grids = {}
    for (kind, oid), samples in raw.items():
        d = dims.get(f"{kind}:{oid}")
        if not d:
            continue
        if kind == "a":
            n = int(d[0])
            if n < 1:
                continue
            arr = np.zeros((n + 1, 3))
            for (t,), off in samples:
                arr[int(np.clip(round(t * n), 0, n))] = off
            grids[(kind, oid)] = (arr, (n,))
        else:
            pu, qv = int(d[0]), int(d[1])
            if pu < 1 or qv < 1:
                continue
            arr = np.zeros((pu + 1, qv + 1, 3))
            for (u, v), off in samples:
                arr[int(np.clip(round(u * pu), 0, pu)),
                    int(np.clip(round(v * qv), 0, qv))] = off
            grids[(kind, oid)] = (arr, (pu, qv))
    return grids


def _sample1(arr, x):
    n = len(arr) - 1
    x = float(np.clip(x, 0.0, n))
    i = int(np.floor(x))
    if i >= n:
        return arr[n]
    f = x - i
    return arr[i] * (1.0 - f) + arr[i + 1] * f


def _sample2(arr, x, y):
    p, q = arr.shape[0] - 1, arr.shape[1] - 1
    x = float(np.clip(x, 0.0, p))
    y = float(np.clip(y, 0.0, q))
    i, j = min(int(np.floor(x)), max(p - 1, 0)), min(int(np.floor(y)), max(q - 1, 0))
    fx, fy = x - i, y - j
    i1, j1 = min(i + 1, p), min(j + 1, q)
    return (arr[i, j] * (1 - fx) * (1 - fy) + arr[i1, j] * fx * (1 - fy)
            + arr[i, j1] * (1 - fx) * fy + arr[i1, j1] * fx * fy)


def apply_deltas(verts, provenance, normals, table):
    """Re-apply a delta table. Returns (verts, stats).

    An exact provenance match re-applies the edit bit for bit — that is the
    same-density case, and it has to be lossless or the layer is not worth
    having. Otherwise the offset is sampled out of the capture-time
    displacement grid: linearly along an arc, bilinearly inside a quad patch.
    Node edits are density-independent and always exact; n-sided patch
    interiors have no parameterisation and are carried only at the same counts,
    which the stats report rather than hide.
    """
    verts = np.asarray(verts, dtype=float).copy()
    deltas = (table or {}).get("offsets", {})
    dims = (table or {}).get("dims", {})
    stats = {"exact": 0, "interpolated": 0, "dropped": 0, "stored": len(deltas)}
    if not deltas:
        return verts, stats

    grids = _reconstruct(deltas, dims)
    used = set()
    exact = interp = 0

    for i, tag in enumerate(provenance):
        key = key_of(tag)
        off = deltas.get(key)
        if off is not None:
            used.add(key)
            off = np.asarray(off, dtype=float)
            exact += 1
        else:
            kind = tag[0]
            entry = grids.get((kind, tag[1])) if kind in ("a", "p") else None
            if entry is None:
                continue
            arr, dims = entry
            if kind == "a":
                off = _sample1(arr, tag[2] * dims[0])
            else:
                off = _sample2(arr, tag[2] * dims[0], tag[3] * dims[1])
            if float(np.linalg.norm(off)) <= 1e-12:
                continue
            interp += 1

        t, b, n = frame(_normal_of(normals, i, verts[i]))
        verts[i] += t * off[0] + b * off[1] + n * off[2]

    # anything whose owner was resampled counts as carried, not dropped
    for key in deltas:
        kind, oid, _ = _parse(key)
        if key in used or (kind in ("a", "p") and (kind, oid) in grids):
            used.add(key)

    stats.update({"exact": exact, "interpolated": interp,
                  "dropped": len(deltas) - len(used)})
    return verts, stats


def load(obj):
    raw = obj.get(DELTA_KEY)
    if not raw:
        return {"offsets": {}, "dims": {}}
    try:
        data = json.loads(raw)
    except Exception:
        return {"offsets": {}, "dims": {}}
    if data.get("version", 1) > SCHEMA:
        return {"offsets": {}, "dims": {}}
    return {"offsets": data.get("offsets", {}), "dims": data.get("dims", {})}


def count(table):
    return len((table or {}).get("offsets", {}))


def store(obj, table):
    offsets = (table or {}).get("offsets", {})
    if offsets:
        obj[DELTA_KEY] = json.dumps({"version": SCHEMA, "offsets": offsets,
                                     "dims": (table or {}).get("dims", {})})
    elif DELTA_KEY in obj:
        del obj[DELTA_KEY]
