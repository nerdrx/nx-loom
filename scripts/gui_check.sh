#!/usr/bin/env bash
# Verify the viewport half (GPU overlay + modal invoke) in a real Blender window.
#
# XDG_RUNTIME_DIR must be cleared too, not just WAYLAND_DISPLAY. Blender's
# Wayland backend falls back to the socket name "wayland-0" when the variable
# is unset and locates it through XDG_RUNTIME_DIR, so clearing only
# WAYLAND_DISPLAY still connects to the user's real compositor and opens a
# window on their desktop. Removing the runtime dir leaves it no socket to
# find, and it falls back to X11 — which is xvfb.
set -u
BLENDER="${NXL_BLENDER:-/run/media/nerdrx/Lex/claude/quadwild_tools/blender-5.2.0-linux-x64/blender}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHOT="${NXL_SHOT:-/tmp/nxl_shot.png}"

LOG="$(mktemp -t nx-loom-gui-XXXXXX.log)"
trap 'rm -f "$LOG"' EXIT

env -u WAYLAND_DISPLAY -u DISPLAY -u XDG_RUNTIME_DIR NXL_SHOT="$SHOT" \
  xvfb-run -a -s "-screen 0 1600x1000x24" \
  "$BLENDER" --factory-startup --python "$HERE/gui_check.py" 2>&1 | tee "$LOG"

grep -qE '^GUI: [0-9]+ passed, 0 failed' "$LOG" || exit 1
