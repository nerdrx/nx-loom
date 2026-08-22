#!/usr/bin/env bash
# NX Loom headless test suite.
#
#   tests/run_all.sh                     run everything in one Blender process
#   NXL_ONLY=test_03 tests/run_all.sh    run only matching test modules
#   NXL_BLENDER=/path/to/blender         override the Blender binary
#
# Exit code is non-zero if any check failed or Blender died.
set -u

BLENDER="${NXL_BLENDER:-/run/media/nerdrx/Lex/claude/quadwild_tools/blender-5.2.0-linux-x64/blender}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

if [ ! -x "$BLENDER" ]; then
    echo "!! blender binary not found: $BLENDER" >&2
    echo "   set NXL_BLENDER=/path/to/blender" >&2
    exit 2
fi

cd "$ROOT" || exit 2
LOG="$(mktemp -t nx-loom-tests-XXXXXX.log)"
trap 'rm -f "$LOG"' EXIT

# Blender exits 0 even when Python raises, so the runner's own summary line is
# the source of truth (learned the hard way in QuadForge).
"$BLENDER" --background --factory-startup \
    --python "$HERE/run_tests.py" --python-exit-code 3 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}

if [ "$rc" -ne 0 ]; then
    echo "!! blender exited $rc" >&2
    exit "$rc"
fi
if grep -qE '^[0-9]+ passed, [1-9][0-9]* failed' "$LOG"; then
    exit 1
fi
if ! grep -qE '^[0-9]+ passed, 0 failed' "$LOG"; then
    echo "!! no summary line — the run did not complete" >&2
    exit 1
fi
exit 0
