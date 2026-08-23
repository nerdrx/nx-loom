"""Why a patch failed, in the artist's words — and what would fix it.

"1 patch unresolved: add an arc, mark it a hole, or change the size" is
generic advice. The solver knows exactly which constraint broke and which
pinned arcs are involved; this module translates that knowledge instead of
discarding it.
"""

from __future__ import annotations

import numpy as np

from .quantize import build_constraints, solve_splits
from .symmetry import representative


def _rep_context(graph):
    rep_of = representative(graph)

    def rep_sides(pid):
        return [[rep_of[a] for a in side]
                for side in graph.patches[pid].arc_sides()]

    return rep_of, rep_sides


def diagnose(graph, report):
    """Per-patch explanations. -> {pid: [line, ...]}

    Works from the same counts the failed solve produced, so the numbers in
    the text are the numbers the artist's mesh actually got.
    """
    out = {}
    counts = report.get("counts") or {}
    if not counts:
        return out
    rep_of, rep_sides = _rep_context(graph)
    rep_counts = {}
    for aid, n in counts.items():
        rep_counts[rep_of.get(aid, aid)] = n

    lock_of = {rep_of.get(a, a): (a, arc.n_lock)
               for a, arc in graph.arcs.items() if arc.n_lock}

    bad = set(report.get("unsatisfied_patches") or [])
    if bad:
        cons = build_constraints(list(graph.patches), rep_sides)
        for c in cons:
            if c.patch not in bad:
                continue
            lines = out.setdefault(c.patch, [])
            pinned = [lock_of[a] for a in c.coeffs if a in lock_of]
            if c.parity:
                total = sum(rep_counts.get(a, 1) * int(co)
                            for a, co in c.coeffs.items())
                n = len(graph.patches[c.patch].sides)
                msg = (f"{n}-sided patch needs an even loop total, "
                       f"it gets {total}")
            else:
                sides = rep_sides(c.patch)
                sums = [sum(rep_counts.get(a, 1) for a in s) for s in sides]
                msg = ("opposite sides disagree: "
                       + " vs ".join(str(x) for x in sums[:4]))
            if pinned:
                msg += (" — pinned arc"
                        + ("s " if len(pinned) > 1 else " ")
                        + ", ".join(f"{a} ({n}*)" for a, n in pinned)
                        + " forces it")
            if msg not in lines:
                lines.append(msg)

    for pid, why in report.get("failed_patches") or []:
        lines = out.setdefault(pid, [])
        if why == "no valid split":
            if pid in graph.patches:
                sides = rep_sides(pid)
                sums = [sum(rep_counts.get(a, 1) for a in s) for s in sides]
                lines.append(
                    f"side counts {tuple(sums)} cannot fan around a centre "
                    f"point — one side is too coarse; add density here")
            else:
                lines.append("side counts cannot fan around a centre point")
        elif why == "non-manifold":
            lines.append("filling this patch would overlap geometry that is "
                         "already built — usually two patches claiming the "
                         "same region")
        elif why == "background":
            continue

    for aid, rep_a, kept, dropped in report.get("lock_conflicts") or []:
        for pid, patch in graph.patches.items():
            arcs = {a for side in patch.arc_sides() for a in side}
            if aid in arcs or rep_a in arcs:
                out.setdefault(pid, []).append(
                    f"pins {kept}* and {dropped}* sit on mirrored partners "
                    f"and disagree — one of them has to go")
    return out


def plan_fixes(graph, report):
    """Candidate one-click repairs, most surgical first.

    -> [(label, kind, payload)] where kind is "unlock" (payload: arc id) or
    "densify" (payload: patch id). The operator validates each candidate on a
    copy before touching the real document.
    """
    fixes = []
    counts = report.get("counts") or {}
    rep_of, rep_sides = _rep_context(graph)
    bad = set(report.get("unsatisfied_patches") or [])
    split_bad = {pid for pid, why in (report.get("failed_patches") or [])
                 if why == "no valid split"}

    involved_locks = []
    for pid in bad:
        if pid not in graph.patches:
            continue
        arcs = {rep_of.get(a, a)
                for side in graph.patches[pid].arc_sides() for a in side}
        for a, arc in graph.arcs.items():
            if arc.n_lock and rep_of.get(a, a) in arcs:
                target = counts.get(a, arc.n_lock)
                regret = abs(arc.n_lock - target)
                involved_locks.append((regret, a, arc.n_lock))
    # release the pin fighting its own target hardest, first
    for _regret, a, n in sorted(involved_locks, reverse=True):
        fixes.append((f"release the {n}* pin on arc {a}", "unlock", a))

    for aid, _rep_a, kept, dropped in report.get("lock_conflicts") or []:
        fixes.append((f"release the {dropped}* pin (its mirror is {kept}*)",
                      "unlock", aid))

    for pid in split_bad:
        if pid in graph.patches:
            fixes.append((f"raise density inside patch {pid}",
                          "densify", pid))
    return fixes
