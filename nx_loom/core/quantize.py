"""Global integer subdivision solve.

Pure numpy. Must stay importable without bpy (SPEC §10) — that is what makes
the solver debuggable outside Blender.

The whole thing hangs off one small matrix. For a patch with n sides, the
half-sum split system is

    a[i] + a[i+2 mod n] = c[i]        (SPEC §2)

where c[i] is the side's subdivision count and a[i] is where side i is split.
Everything falls out of that single system:

  * n == 4  ->  left-nullspace rows are (1,0,-1,0) and (0,1,0,-1): the familiar
                "opposite sides must match" constraint, derived rather than
                hard-coded.
  * n odd   ->  the system is invertible, so the only condition is that the
                counts admit an integer solution, i.e. sum(c) is even.
  * n even  ->  the system has a two-dimensional left-nullspace; those rows are
                the alternating-sum conditions on the even- and odd-indexed
                sides separately.
"""

from __future__ import annotations

import numpy as np

MAX_REPAIR_ITERS = 4000


# --------------------------------------------------------------------------
# the split system
# --------------------------------------------------------------------------

def split_matrix(n: int) -> np.ndarray:
    """M with M @ a == c for the half-sum template (a[i] + a[i+2] = c[i])."""
    M = np.zeros((n, n))
    for i in range(n):
        M[i, i] += 1.0
        M[i, (i + 2) % n] += 1.0
    return M


def _orbits(n: int):
    """Orbits of the permutation i -> i+2 (mod n), in traversal order."""
    seen, orbits = set(), []
    for start in range(n):
        if start in seen:
            continue
        orbit, i = [], start
        while i not in seen:
            seen.add(i)
            orbit.append(i)
            i = (i + 2) % n
        orbits.append(orbit)
    return orbits


def patch_constraint_rows(n: int) -> np.ndarray:
    """Integer rows r with r @ c == 0 required for an n-sided patch to fill.

    Each orbit of ``i -> i+2`` is a cycle block of the split system. A cycle of
    even length is singular, and its left-nullspace is spanned by the
    alternating vector — so the row is exactly +1/-1 down the orbit. Deriving
    them this way rather than from an SVD keeps the coefficients integral,
    which is what lets the parity pass reduce the same rows mod 2.
    """
    if n < 3:
        return np.zeros((0, max(n, 0)), dtype=float)
    rows = []
    for orbit in _orbits(n):
        if len(orbit) % 2 == 0:
            r = np.zeros(n)
            for k, i in enumerate(orbit):
                r[i] = 1.0 if k % 2 == 0 else -1.0
            rows.append(r)
    return np.array(rows) if rows else np.zeros((0, n))


def parity_groups(n: int):
    """Side-index groups whose subdivision counts must sum to an even number.

    The split system's variables are permuted by ``i -> i+2 (mod n)``. That
    permutation decomposes into ``gcd(2, n)`` orbits. An orbit of *odd* length
    makes its block of the system invertible, and the unique solution is
    (sum over the orbit) / 2 — so that sum has to be even for the split points
    to be integers. An orbit of even length is rank-deficient instead, and
    contributes an equality row from ``patch_constraint_rows`` rather than a
    parity condition.

      n = 3, 5, 7   one odd orbit  -> total must be even
      n = 6, 10     two odd orbits -> even- and odd-indexed sides each even
      n = 4, 8, 12  even orbits    -> equality rows, no parity condition
    """
    if n < 3:
        return []
    return [o for o in _orbits(n) if len(o) % 2 == 1]


def solve_splits(counts):
    """Split points a[i] for an n-sided patch. Returns None if infeasible."""
    c = np.asarray(counts, dtype=float)
    n = len(c)
    M = split_matrix(n)
    a, *_ = np.linalg.lstsq(M, c, rcond=None)
    a_int = np.rint(a).astype(int)
    if not np.allclose(M @ a_int, c, atol=1e-6):
        return None
    if np.any(a_int < 1):
        return None
    return a_int


# --------------------------------------------------------------------------
# constraint assembly
# --------------------------------------------------------------------------

class Constraint:
    """sum(coef * n[arc]) == 0, or (parity) sum(coef * n[arc]) is even."""

    __slots__ = ("coeffs", "parity", "patch")

    def __init__(self, coeffs, parity=False, patch=-1):
        self.coeffs = coeffs          # dict[arc_id] -> float/int coefficient
        self.parity = parity
        self.patch = patch

    def residual(self, n):
        v = sum(co * n[a] for a, co in self.coeffs.items())
        if self.parity:
            return int(round(v)) % 2
        return v

    def violated(self, n, tol=1e-6):
        r = self.residual(n)
        return abs(r) > (0 if self.parity else tol)


def build_constraints(patches, sides_of):
    """patches: iterable of patch ids. sides_of(patch) -> list[list[arc_id]]."""
    out = []
    for pid in patches:
        sides = sides_of(pid)
        n = len(sides)
        if n < 3:
            continue
        for row in patch_constraint_rows(n):
            coeffs = {}
            for i, side in enumerate(sides):
                if abs(row[i]) < 1e-9:
                    continue
                for arc in side:
                    coeffs[arc] = coeffs.get(arc, 0.0) + float(row[i])
            coeffs = {a: c for a, c in coeffs.items() if abs(c) > 1e-9}
            if coeffs:
                out.append(Constraint(coeffs, patch=pid))
        for group in parity_groups(n):
            coeffs = {}
            for i in group:
                for arc in sides[i]:
                    coeffs[arc] = coeffs.get(arc, 0) + 1
            if coeffs:
                out.append(Constraint(coeffs, parity=True, patch=pid))
    return out


# --------------------------------------------------------------------------
# the solve
# --------------------------------------------------------------------------

def arc_floors(arc_ids, patches, sides_of):
    """Minimum subdivision count per arc.

    Every side of a non-quad patch needs at least 2 segments — a side of 1
    cannot carry a split point anywhere except on top of a corner. A side made
    of several arcs already clears that with 1 each, so only *single-arc* sides
    of non-quad patches raise their arc's floor.
    """
    floors = {a: 1 for a in arc_ids}
    for pid in patches:
        sides = sides_of(pid)
        if len(sides) == 4 or len(sides) < 3:
            continue
        for side in sides:
            if len(side) == 1:
                floors[side[0]] = max(floors[side[0]], 2)
    return floors


def _real_solve(arc_ids, targets, constraints, locks):
    """Equality-constrained least squares, ignoring integrality and parity."""
    free = [a for a in arc_ids if a not in locks]
    idx = {a: i for i, a in enumerate(free)}
    m = len(free)
    if m == 0:
        return dict(locks)

    t = np.array([targets[a] for a in free], dtype=float)
    w = 1.0 / np.maximum(t, 0.5)          # relative error, not absolute
    W = np.diag(w * w)

    rows, rhs = [], []
    for c in constraints:
        if c.parity:
            continue                       # parity is handled by repair
        row = np.zeros(m)
        b = 0.0
        for a, co in c.coeffs.items():
            if a in idx:
                row[idx[a]] = co
            else:
                b -= co * locks[a]
        if np.any(np.abs(row) > 1e-9):
            rows.append(row)
            rhs.append(b)

    if not rows:
        return {a: float(targets[a]) for a in free}

    A = np.vstack(rows)
    b = np.array(rhs)
    k = A.shape[0]
    KKT = np.zeros((m + k, m + k))
    KKT[:m, :m] = 2.0 * W
    KKT[:m, m:] = A.T
    KKT[m:, :m] = A
    rhs_v = np.concatenate([2.0 * W @ t, b])
    sol, *_ = np.linalg.lstsq(KKT, rhs_v, rcond=None)
    out = {}
    for a in free:
        v = float(sol[idx[a]])
        # a singular or poisoned system must degrade to the plain target,
        # never poison the integer rounding downstream
        out[a] = v if np.isfinite(v) else float(targets[a])
    return out


def _flip_cost(n, targets, arc, floors):
    """Cheapest +-1 move for one arc, as extra rounding regret. None if stuck."""
    base = abs(n[arc] - targets[arc])
    best = None
    for step in (+1, -1):
        if n[arc] + step < floors.get(arc, 1):
            continue
        c = abs(n[arc] + step - targets[arc]) - base
        if best is None or c < best[0]:
            best = (c, step)
    return best


def _gf2_fix(n, constraints, targets, locks, floors):
    """Make every constraint hold *modulo 2* with one linear solve.

    Greedy hill-climbing cannot do this. Parity coupling is global — on a
    triangulated closed surface every patch shares all three of its arcs with a
    neighbour, so flipping one arc fixes one patch and breaks another, and a
    local search stalls with most patches still odd. Over GF(2) it is just a
    linear system, and Gaussian elimination either finds a flip set or proves
    none exists.

    Columns are ordered cheapest-first so the arcs that hurt least to move
    become pivots and the expensive ones stay free (unflipped).
    """
    free = [a for a in n if a not in locks]
    if not free:
        return [c for c in constraints if c.residual(n) % 2]

    costs = {}
    movable = []
    for a in free:
        fc = _flip_cost(n, targets, a, floors)
        if fc is not None:
            costs[a] = fc
            movable.append(a)
    if not movable:
        return [c for c in constraints if int(round(c.residual(n))) % 2]

    movable.sort(key=lambda a: costs[a][0])
    bit = {a: i for i, a in enumerate(movable)}

    rows, rhs, owners = [], [], []
    for c in constraints:
        mask = 0
        for a, co in c.coeffs.items():
            if int(round(co)) % 2 and a in bit:
                mask ^= 1 << bit[a]
        r = int(round(c.residual(n))) % 2
        if mask == 0 and r == 0:
            continue
        rows.append(mask)
        rhs.append(r)
        owners.append(c)

    if not rows:
        return []

    # reduced row echelon over GF(2), pivoting in cheapest-column order
    used = [False] * len(rows)
    pivot_row = {}
    for a in movable:
        col = 1 << bit[a]
        pr = next((i for i in range(len(rows)) if not used[i] and rows[i] & col), None)
        if pr is None:
            continue
        used[pr] = True
        pivot_row[a] = pr
        for i in range(len(rows)):
            if i != pr and rows[i] & col:
                rows[i] ^= rows[pr]
                rhs[i] ^= rhs[pr]

    stuck = [owners[i] for i in range(len(rows)) if rows[i] == 0 and rhs[i]]
    for a, pr in pivot_row.items():
        if rhs[pr]:
            n[a] += costs[a][1]
    return stuck


def _repair(n, constraints, targets, locks, arc_ids, floors, step_size=1):
    """Greedy coordinate descent on integer counts until every constraint holds.

    Move the arc whose +-1 step buys the most violation reduction per unit of
    rounding regret. Bounded; unsatisfiable constraints are returned, never
    silently dropped.
    """
    by_arc = {}
    for ci, c in enumerate(constraints):
        for a in c.coeffs:
            by_arc.setdefault(a, set()).add(ci)

    def local_violation(arc):
        return sum(abs(constraints[ci].residual(n)) for ci in by_arc.get(arc, ()))

    for _ in range(MAX_REPAIR_ITERS):
        bad = [c for c in constraints if c.violated(n)]
        if not bad:
            return []
        worst = max(bad, key=lambda c: abs(c.residual(n)))
        best = None
        for a in worst.coeffs:
            if a in locks:
                continue
            for step in (+step_size, -step_size):
                if n[a] + step < floors.get(a, 1):
                    continue
                before = local_violation(a)
                n[a] += step
                after = local_violation(a)
                regret = abs(n[a] - targets[a])
                n[a] -= step
                gain = before - after
                if gain <= 0:
                    continue
                score = (gain, -regret)
                if best is None or score > best[0]:
                    best = (score, a, step)
        if best is None:
            break
        _, a, step = best
        n[a] += step

    return [c for c in constraints if c.violated(n)]


def _bump_infeasible_splits(counts, patches, sides_of, targets, locks, floors):
    """Raise side counts until every non-quad patch admits splits with a_i >= 1.

    Parity and the equality rows only make the split system *solvable*; they do
    not make it *positive*. A triangle patch with counts (1, 1, 2) has an even
    total and still fails, because one split point would have to sit on top of
    a corner. The fix is a linear one: a_i responds to side j with weight
    pinv(M)[i, j], so raise the side with the strongest positive influence.
    Steps of 2 keep the parities the GF(2) pass established.
    """
    touched = []
    for pid in patches:
        sides = sides_of(pid)
        n = len(sides)
        if n == 4 or n < 3:
            continue
        Minv = np.linalg.pinv(split_matrix(n))
        for _ in range(n + 2):
            c = np.array([sum(counts[a] for a in s) for s in sides], dtype=float)
            if solve_splits(c.astype(int)) is not None:
                break
            a = Minv @ c
            i = int(np.argmin(a))
            order = np.argsort(-Minv[i])
            moved = False
            for j in order:
                if Minv[i, j] <= 1e-9:
                    break
                cand = [x for x in sides[j] if x not in locks]
                if not cand:
                    continue
                arc = min(cand, key=lambda x: abs(counts[x] + 2 - targets[x]))
                counts[arc] += 2
                touched.append(pid)
                moved = True
                break
            if not moved:
                break
    return touched


def quantize(arc_ids, arc_lengths, target_edge, patches, sides_of, locks=None,
             seed=None, shifts=(0.0, 0.25, -0.25, 0.5, -0.5), deadline=None):
    """Solve integer subdivision counts for every arc.

    Returns (counts, report). Never raises on a well-formed graph and never
    returns counts that violate a constraint the solver claims to have met
    (SPEC §2) — patches it could not satisfy are named in
    ``report["unsatisfied_patches"]``.
    """
    locks = dict(locks or {})
    arc_ids = list(arc_ids)
    targets = {a: max(1.0, arc_lengths[a] / max(target_edge, 1e-9)) for a in arc_ids}

    constraints = build_constraints(patches, sides_of)
    floors = arc_floors(arc_ids, patches, sides_of)

    # The relaxation has to see the floors. Solving from raw targets and only
    # clamping afterwards produces a starting point that is globally
    # inconsistent — a pole fan forces its spokes to 2 while every arc around
    # it sits at 1, and no amount of local repair walks that back. Raising the
    # targets lets the equality rows propagate the floor through the whole
    # chain before anything is rounded. Regret is still measured against the
    # true targets.
    solve_targets = {a: max(targets[a], float(floors[a])) for a in arc_ids}
    real = _real_solve(arc_ids, solve_targets, constraints, locks)

    def _settle(start):
        """Run parity -> equality -> split-feasibility to a fixed point."""
        counts = dict(start)
        stuck, bumped = [], []
        prev_bad = None
        for _ in range(12):
            stuck = _gf2_fix(counts, constraints, targets, locks, floors)
            bad2 = _repair(counts, constraints, targets, locks, arc_ids, floors,
                           step_size=2)
            newly = _bump_infeasible_splits(counts, patches, sides_of, targets,
                                            locks, floors)
            bumped.extend(newly)
            if not bad2 and not newly:
                break
            if bad2:
                # Parity-preserving +-2 steps alone cannot reach every feasible
                # point. A quad side pinned at its floor by a neighbouring
                # triangle needs a +-1 move plus a compensating parity fix
                # somewhere else, so take the unit step here and let the next
                # GF(2) pass repair the parity it breaks.
                _repair(counts, constraints, targets, locks, arc_ids, floors,
                        step_size=1)
            if not newly and prev_bad is not None and len(bad2) >= prev_bad:
                if not bad2:
                    break
            prev_bad = len(bad2)
        # a bump lands on an arc shared with a neighbouring patch, so the
        # equality rows have to be settled *after* the last bump, not before it
        stuck = _gf2_fix(counts, constraints, targets, locks, floors)
        bad = _repair(counts, constraints, targets, locks, arc_ids, floors, step_size=2)
        bad = list({id(c): c for c in (stuck + bad)}.values())
        return counts, bad, bumped

    # Greedy repair is a hill-climb and can stall in a local minimum where no
    # single step reduces the violation even though a feasible point exists.
    # Restarting from a different rounding of the same real solution costs
    # almost nothing and reliably shakes it loose; the offsets are fixed, so
    # this stays deterministic.
    best = None
    for shift in shifts:
        # A time budget skips the remaining restarts once one has finished —
        # graceful degradation, never a raise: the first settle always runs,
        # and the seed rescue below is unconditional (the "topology-preserving
        # edits never un-solve" guarantee must not depend on the clock).
        if deadline is not None and best is not None and deadline.over():
            break
        start = {}
        for a in arc_ids:
            if a in locks:
                start[a] = max(1, int(locks[a]))
            else:
                start[a] = max(floors[a],
                               int(round(real.get(a, solve_targets[a]) + shift)))
        counts, bad, bumped = _settle(start)
        regret = sum(abs(counts[x] - targets[x]) for x in arc_ids)
        score = (len(bad), regret)
        if best is None or score < best[0]:
            best = (score, counts, bad, bumped, shift)
        if not bad:
            break

    seed_rescued = False
    if best[0][0] > 0 and seed:
        # Whether the system is solvable is purely topological; the heuristic
        # failing is not the same as infeasibility. If the caller has counts
        # that solved before -- an edit that moved a node changed only the
        # TARGETS, not one constraint -- settling from them recovers a valid
        # solution the fresh multi-start missed. This is what makes "I nudged
        # a vertex and now it will not solve" impossible for edits that leave
        # the topology alone.
        start = {}
        for a in arc_ids:
            if a in locks:
                start[a] = max(1, int(locks[a]))
            else:
                base = seed.get(a)
                if base is None:
                    base = int(round(real.get(a, solve_targets[a])))
                start[a] = max(floors[a], int(base))
        counts, bad, bumped = _settle(start)
        regret = sum(abs(counts[x] - targets[x]) for x in arc_ids)
        score = (len(bad), regret)
        if score < best[0]:
            best = (score, counts, bad, bumped, "seed")
            seed_rescued = len(bad) == 0
    _, counts, unsatisfied, bumped, used_shift = best

    bad_patches = sorted({c.patch for c in unsatisfied})
    split_failures = []
    for pid in patches:
        if pid in bad_patches:
            continue
        sides = sides_of(pid)
        if len(sides) == 4 or len(sides) < 3:
            continue
        side_counts = [sum(counts[a] for a in s) for s in sides]
        if solve_splits(side_counts) is None:
            split_failures.append(pid)

    total_regret = sum(abs(counts[a] - targets[a]) for a in arc_ids)
    report = {
        "arcs": len(arc_ids),
        "constraints": len(constraints),
        "unsatisfied_patches": bad_patches,
        "parity_stuck": sum(1 for c in unsatisfied if c.parity),
        "split_bumps": len(bumped),
        "round_shift": used_shift,
        "seed_rescued": seed_rescued,
        "split_failures": split_failures,
        "total_regret": total_regret,
        "mean_regret": total_regret / max(len(arc_ids), 1),
    }
    return counts, report
