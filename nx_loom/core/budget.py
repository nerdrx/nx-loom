"""Time budgets for long calculations (bpy-free, SPEC §10).

Heavy work is written as generators that yield ``(fraction, label)`` progress
between small units of work and return their result. Interactively they are
pumped by a timer job (ops/jobs.py) so the UI never blocks and a Cancel is
honoured between units; headless and in tests they are drained synchronously.
A Deadline bounds even the synchronous path — a runaway solve raises
OutOfTime instead of freezing Blender.
"""

from __future__ import annotations

import time


class OutOfTime(Exception):
    """A calculation exceeded its time budget and was cancelled."""


class Deadline:
    """Wall-clock budget. ``seconds=None`` never expires."""

    def __init__(self, seconds=None):
        self.t0 = time.monotonic()
        self.limit = float(seconds) if seconds else None

    def elapsed(self):
        return time.monotonic() - self.t0

    def over(self):
        return self.limit is not None and self.elapsed() > self.limit

    def check(self, doing=""):
        if self.over():
            raise OutOfTime(
                f"{doing or 'calculation'} ran over the "
                f"{self.limit:.0f}s budget and was cancelled")


def drain(gen, progress=None):
    """Run a progress generator to completion and return its result."""
    while True:
        try:
            item = next(gen)
        except StopIteration as stop:
            return stop.value
        if progress is not None and item is not None:
            progress(*item)
