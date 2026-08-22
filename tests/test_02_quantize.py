"""The quantizer: constraints are derived, satisfied, and honestly reported."""

import numpy as np

from nx_loom.core.quantize import (build_constraints, parity_groups,
                                   patch_constraint_rows, quantize, solve_splits)


def _cube():
    X, Y, Z = [0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11]
    lengths = {}
    for a in X:
        lengths[a] = 3.0
    for a in Y:
        lengths[a] = 1.7
    for a in Z:
        lengths[a] = 0.9
    faces = {
        0: [[X[0]], [Z[0]], [X[1]], [Z[1]]],
        1: [[X[2]], [Z[2]], [X[3]], [Z[3]]],
        2: [[Y[0]], [Z[1]], [Y[1]], [Z[2]]],
        3: [[Y[2]], [Z[3]], [Y[3]], [Z[0]]],
        4: [[X[0]], [Y[0]], [X[2]], [Y[2]]],
        5: [[X[1]], [Y[1]], [X[3]], [Y[3]]],
    }
    return lengths, faces, X, Y, Z


def run():
    out = []

    # the quad rule is derived, not hard-coded
    rows4 = patch_constraint_rows(4)
    out.append(("4-sided yields 2 equality rows", rows4.shape[0] == 2, str(rows4.shape)))
    ok = all(abs(abs(r[0]) - abs(r[2])) < 1e-9 or abs(abs(r[1]) - abs(r[3])) < 1e-9
             for r in rows4)
    out.append(("rows pair opposite sides", ok, ""))
    out.append(("odd n needs even total", parity_groups(3) == [[0, 2, 1]], ""))
    out.append(("6-sided splits into two parity groups",
                len(parity_groups(6)) == 2, str(parity_groups(6))))
    out.append(("8-sided uses equality rows, not parity",
                parity_groups(8) == [] and patch_constraint_rows(8).shape[0] == 2, ""))

    out.append(("split (2,2,2)", list(solve_splits((2, 2, 2))) == [1, 1, 1], ""))
    out.append(("split (3,3,3) rejected (odd total)", solve_splits((3, 3, 3)) is None, ""))
    out.append(("split (1,1,1,1,1,1) rejected", solve_splits((1,) * 6) is None, ""))

    lengths, faces, X, Y, Z = _cube()
    arcs = list(lengths)
    sides_of = lambda p: faces[p]

    # every density closes
    all_closed, details = True, []
    for te in (1.0, 0.5, 0.25, 0.12, 0.06, 0.03):
        counts, rep = quantize(arcs, lengths, te, list(faces), sides_of)
        bad = [c for c in build_constraints(list(faces), sides_of) if c.violated(counts)]
        if bad or rep["unsatisfied_patches"]:
            all_closed = False
            details.append(f"edge={te}")
    out.append(("cube closes at every density", all_closed, ",".join(details)))

    counts, rep = quantize(arcs, lengths, 0.25, list(faces), sides_of)
    out.append(("regret stays small", rep["mean_regret"] < 0.5, f"{rep['mean_regret']:.3f}"))

    # multi-arc sides: constraints are on side SUMS, not single arcs
    lengths2 = dict(lengths)
    lengths2[12] = lengths2[13] = 1.0
    lengths2[X[0]] = 1.0
    faces2 = dict(faces)
    faces2[0] = [[X[0], 12, 13], [Z[0]], [X[1]], [Z[1]]]
    faces2[4] = [[X[0], 12, 13], [Y[0]], [X[2]], [Y[2]]]
    c2, r2 = quantize(list(lengths2), lengths2, 0.25, list(faces2), lambda p: faces2[p])
    chain = c2[X[0]] + c2[12] + c2[13]
    out.append(("chained side sums to its opposite",
                chain == c2[X[1]] == c2[X[2]] and not r2["unsatisfied_patches"],
                f"chain={chain} opp={c2[X[1]]}"))

    # an impossible lock is reported, never silently wrong
    c3, r3 = quantize(arcs, lengths, 0.25, list(faces), sides_of, locks={X[0]: 5, X[1]: 8})
    out.append(("infeasible locks are reported",
                len(r3["unsatisfied_patches"]) > 0, str(r3["unsatisfied_patches"])))
    out.append(("locks are honoured", c3[X[0]] == 5 and c3[X[1]] == 8, ""))
    return out
