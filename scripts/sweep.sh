#!/usr/bin/env bash
# Primitive sweep, memory-safe: one Blender process PER PRIMITIVE, strictly
# sequential, with a free-memory gate before each chunk. Replaces the old
# single-process 117-layout sweep that could fill RAM.
#   scripts/sweep.sh            run everything
#   NXL_BLENDER=...             override the blender binary
set -euo pipefail
cd "$(dirname "$0")/.."
BLENDER="${NXL_BLENDER:-/run/media/nerdrx/Lex/claude/quadwild_tools/blender-5.2.0-linux-x64/blender}"
PRIMS="uv_sphere_12_6 uv_sphere_16_8 uv_sphere_24_12 ico_1 ico_2 ico_3 \
cyl_8 cyl_16 cyl_24 cone_8 cone_16 cone_24 torus_12_6 torus_16_8 torus_24_12"

total=0; bad=0
for prim in $PRIMS; do
    avail_kb=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
    if [ "$avail_kb" -lt 4000000 ]; then
        echo "SWEEP ABORTED: only $((avail_kb / 1024)) MB free before $prim"
        exit 1
    fi
    out=$(NXL_PRIM="$prim" "$BLENDER" --background --factory-startup \
          --python scripts/sweep_chunk.py 2>&1 | grep -E "^(CHUNK|  FAIL)") \
          || { echo "SWEEP ABORTED: $prim chunk produced no verdict"; exit 1; }
    echo "$out" | grep "^  FAIL" || true
    line=$(echo "$out" | grep "^CHUNK") \
          || { echo "SWEEP ABORTED: $prim chunk crashed"; exit 1; }
    total=$((total + $(echo "$line" | awk '{print $3}')))
    bad=$((bad + $(echo "$line" | awk '{print $4}')))
done
echo "SWEEP $total layouts, $bad with unresolved/broken patches"
