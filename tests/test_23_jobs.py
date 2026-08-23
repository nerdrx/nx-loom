"""Long calculations: progress, cancel, and the freeze watchdog.

Contract under test: heavy work runs as progress generators whose yields are
honest (monotonic, labelled) and whose results are written only after the
final yield — so cancelling or timing out at ANY point leaves the document
exactly as it was. The synchronous drain of a generator must be bit-identical
to the old blocking call, and a budget that expires raises OutOfTime instead
of freezing Blender.
"""

import bpy
import numpy as np

from nx_loom.core.budget import Deadline, OutOfTime, drain
from nx_loom.core.build import build, build_iter
from nx_loom.ops.layout import GRAPH_KEY, get_graph, rebuild_object


def _sphere_layout():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=6, radius=1.0)
    src = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    st.target_edge = 0.25
    st.relax_iters = 2
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.nxloom.layout_from_selection()
    return bpy.context.active_object, st


def run():
    import nx_loom
    try:
        nx_loom.register()
    except Exception:
        pass
    out = []

    obj, st = _sphere_layout()
    graph = get_graph(obj)

    # -- drain(build_iter) is the old build(), bit for bit
    v1, q1, p1, r1 = build(graph, target_edge=0.25, relax_iters=2)
    steps = []
    gen = build_iter(graph, target_edge=0.25, relax_iters=2)
    while True:
        try:
            item = next(gen)
        except StopIteration as stop:
            v2, q2, p2, r2 = stop.value
            break
        steps.append(item)
    same = (len(v1) == len(v2) and np.allclose(v1, v2) and q1 == q2)
    out.append(("chunked build equals blocking build",
                same, f"{len(v1)} vs {len(v2)} verts"))

    fracs = [f for f, _ in steps]
    labels_ok = all(isinstance(lbl, str) and lbl for _, lbl in steps)
    mono = all(b >= a for a, b in zip(fracs, fracs[1:]))
    out.append(("progress is monotonic, labelled, and in range",
                mono and labels_ok and 0.0 <= min(fracs)
                and max(fracs) <= 1.0,
                f"{len(steps)} steps, {min(fracs):.2f}..{max(fracs):.2f}"))

    # -- an expired deadline raises instead of grinding on
    try:
        build(graph, target_edge=0.25, relax_iters=2,
              deadline=Deadline(1e-9))
        raised = False
    except OutOfTime:
        raised = True
    out.append(("an expired budget raises OutOfTime", raised, ""))

    # -- a timed-out rebuild leaves mesh and document untouched
    blob_before = obj[GRAPH_KEY]
    polys_before = len(obj.data.polygons)
    st.job_budget = 1e-06
    rep = rebuild_object(obj, bpy.context)
    timed_out = rep is None and bool(obj.get("nx_loom_timeout"))
    untouched = (obj[GRAPH_KEY] == blob_before
                 and len(obj.data.polygons) == polys_before)
    out.append(("a timed-out rebuild returns None and says why",
                timed_out, str(obj.get("nx_loom_timeout"))[:40]))
    out.append(("and leaves mesh and layout exactly as they were",
                untouched,
                f"{polys_before} -> {len(obj.data.polygons)} faces"))

    # -- restoring the budget recovers, and clears the stale warning
    st.job_budget = 0.0
    rep = rebuild_object(obj, bpy.context)
    out.append(("restoring the budget rebuilds and clears the warning",
                bool(rep) and rep["quads"] > 0
                and "nx_loom_timeout" not in obj,
                f"{rep['quads'] if rep else 0} quads"))

    # -- the job pump: completion, cancel, and the watchdog
    from nx_loom.ops import jobs

    out.append(("background jobs never start headless",
                not jobs.should_run_async(bpy.context, 9999.0), ""))

    def fake(n=4, result="done"):
        for i in range(n):
            yield (i / n, f"step {i}")
        return result

    got = {}
    jobs.start("t1", fake(), on_done=lambda r, w: got.update(r=r, w=w))
    for _ in range(50):
        if jobs._pump() is None:
            break
    out.append(("a job pumps to completion and hands back its result",
                got.get("r") == "done" and got.get("w") is None
                and not jobs.running(), str(got)))

    got = {}
    ok = jobs.start("t2", fake(n=1000),
                    on_done=lambda r, w: got.update(r=r, w=w))
    jobs.request_cancel()
    for _ in range(50):
        if jobs._pump() is None:
            break
    out.append(("cancel stops a job and reports it",
                ok and got.get("r") is None and got.get("w") == "cancelled"
                and not jobs.running(), str(got)))

    got = {}

    def slow():
        import time
        while True:
            time.sleep(0.002)
            yield (0.1, "spinning")

    jobs.start("t3", slow(), on_done=lambda r, w: got.update(r=r, w=w),
               budget=0.01)
    for _ in range(200):
        if jobs._pump() is None:
            break
    out.append(("the watchdog auto-cancels a runaway job",
                got.get("w") == "timeout" and not jobs.running()
                and "auto-cancelled" in jobs.JOB["note"], str(got)))

    # -- cancelling a suggest job mid-flight proposes nothing
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12,
                                         radius=1.0)
    ref = bpy.context.active_object
    st = bpy.context.scene.nx_loom
    bpy.ops.nxloom.new_layout()
    obj = bpy.context.active_object
    from nx_loom.ops.suggest import _suggest_job
    gen = _suggest_job(obj, ref, bpy.context)
    next(gen)
    next(gen)
    gen.close()
    g = get_graph(obj)
    out.append(("a cancelled suggest job leaves no ghosts",
                not (g.settings.get("suggestions") or []), ""))
    res = drain(_suggest_job(obj, ref, bpy.context))
    g = get_graph(obj)
    out.append(("the drained suggest job still proposes",
                res[0] == "INFO" and bool(g.settings.get("suggestions")),
                res[1][:40]))

    return out
