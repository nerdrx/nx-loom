"""Background jobs: pump a progress generator from a timer so the UI stays
alive, show a bar with a Cancel button, and auto-cancel anything that runs
over the budget. One job at a time — the pipeline is not reentrant.

Cancel is only ever honoured *between* units of work, which is safe by
construction: every job writes its results (mesh, graph, ghosts) after its
final yield, so a cancelled job leaves the document exactly as it was.
"""

from __future__ import annotations

import time
import traceback

import bpy

from ..core.budget import OutOfTime

# How long one timer tick may work before handing control back to the UI.
TICK_BUDGET = 0.08

JOB = {
    "active": False,
    "title": "",
    "label": "",
    "frac": 0.0,
    "t0": 0.0,
    "cancel": False,
    "note": "",          # last outcome, shown in the panel until the next job
}

_STATE = {"gen": None, "on_done": None, "budget": None}


def running():
    return JOB["active"]


def request_cancel():
    if JOB["active"]:
        JOB["cancel"] = True


def _status(text):
    try:
        bpy.context.workspace.status_text_set(text)
    except Exception:
        pass


def _cursor(frac):
    try:
        wm = bpy.context.window_manager
        wm.progress_update(int(frac * 100))
    except Exception:
        pass


def _redraw():
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:
        pass


def _finish(note):
    JOB["active"] = False
    JOB["cancel"] = False
    JOB["note"] = note or ""
    _STATE["gen"] = None
    _STATE["on_done"] = None
    try:
        bpy.context.window_manager.progress_end()
    except Exception:
        pass
    _status(None)
    _redraw()


def start(title, gen, on_done=None, budget=None):
    """Run ``gen`` in the background. Returns False if a job is running."""
    if JOB["active"]:
        return False
    JOB.update(active=True, title=title, label="starting", frac=0.0,
               t0=time.monotonic(), cancel=False, note="")
    _STATE.update(gen=gen, on_done=on_done, budget=budget)
    try:
        bpy.context.window_manager.progress_begin(0, 100)
    except Exception:
        pass
    bpy.app.timers.register(_pump, first_interval=0.0)
    return True


def _pump():
    gen = _STATE["gen"]
    if gen is None:
        return None
    on_done = _STATE["on_done"]
    budget = _STATE["budget"]
    tick0 = time.monotonic()
    try:
        while True:
            if JOB["cancel"]:
                gen.close()
                _finish(f"{JOB['title']}: cancelled")
                if on_done:
                    on_done(None, "cancelled")
                return None
            if budget and time.monotonic() - JOB["t0"] > budget:
                gen.close()
                _finish(f"{JOB['title']}: auto-cancelled after "
                        f"{budget:.0f}s — raise the budget in the panel "
                        f"or simplify the layout")
                if on_done:
                    on_done(None, "timeout")
                return None
            item = next(gen)
            if item is not None:
                frac, label = item
                JOB["frac"] = max(JOB["frac"], min(float(frac), 1.0))
                JOB["label"] = str(label)
            if time.monotonic() - tick0 > TICK_BUDGET:
                break
    except StopIteration as stop:
        _finish("")
        if on_done:
            on_done(stop.value, None)
        return None
    except OutOfTime as exc:
        _finish(f"{JOB['title']}: {exc}")
        if on_done:
            on_done(None, "timeout")
        return None
    except Exception:
        traceback.print_exc()
        _finish(f"{JOB['title']}: failed — see console")
        if on_done:
            on_done(None, "error")
        return None

    pct = int(JOB["frac"] * 100)
    _status(f"NX Loom — {JOB['title']}: {JOB['label']}  {pct}%   "
            f"(Cancel in the sidebar)")
    _cursor(JOB["frac"])
    _redraw()
    return 0.01


def should_run_async(context, last_ms):
    """Heavy enough to be worth a background job, and a UI to show it in."""
    if bpy.app.background or context is None or context.window is None:
        return False
    if JOB["active"]:
        return False           # one at a time; the caller falls back to sync
    return (last_ms or 0.0) > 400.0


class NXLOOM_OT_job_cancel(bpy.types.Operator):
    """Stop the running calculation — the layout and mesh stay as they were"""

    bl_idname = "nxloom.job_cancel"
    bl_label = "Cancel"

    @classmethod
    def poll(cls, context):
        return JOB["active"]

    def execute(self, context):
        request_cancel()
        return {"FINISHED"}


def register():
    bpy.utils.register_class(NXLOOM_OT_job_cancel)


def unregister():
    bpy.utils.unregister_class(NXLOOM_OT_job_cancel)
