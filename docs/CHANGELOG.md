# Changelog

## 0.3.0 — unreleased

The delta layer. Hand edits now survive a rebuild, which is what makes
"non-destructive" mean anything.

- Edits are keyed on vertex **provenance** (node / point along an arc /
  parameterised point in a patch), not vertex index, and stored as offsets in a
  local frame derived from the surface normal.
- **Re-applying at the same subdivision counts is lossless** — measured exact,
  and only the edited vertices move.
- Across a density change, offsets resample out of the capture-time
  displacement grid: linearly along arcs, bilinearly inside quad patches.
- `Capture Edits` / `Clear Edits` in the sidebar. Changing the vertex count is
  refused with an error that says to draw the change instead.
- `build()` now returns per-vertex provenance; quad fills return interior
  `(u, v)` so edits are resolution-independent.

Two bugs found by testing rather than by reading, both of which broke the
lossless guarantee:

- Interpolating stored deltas with a distance weighting has **global support**,
  so re-applying at the same density displaced every other vertex in an edited
  patch (0.33 error where it had to be zero).
- Capture-time resolution **cannot be inferred from the stored samples**. An
  arc with one edit at `t = 2/3` reconstructs as `n = 2` and smears the offset
  along the whole arc. It is recorded explicitly now.

94 headless checks + 8 GUI checks.


## 0.2.0 — unreleased

The drawing tool. A layout can now be authored from nothing instead of traced
off an existing mesh.

- **Loom Draw** toolbar tool (`WorkSpaceTool`, tool-scoped keymap): click to
  chain straight-on-surface segments, drag for freehand, Ctrl to erase or
  dissolve, Shift to drag a node, Alt to retype an arc.
- Straight segments interpolate *rays* and re-cast every sample, so they wrap
  over a bulge instead of tunnelling through it.
- Snap radius is specified in pixels and converted per click, so snapping feels
  the same at any zoom. Ending a stroke on an existing arc splits it into a
  T-junction.
- `core/authoring.py` — split, dissolve, move, erase, prune, decimate. All
  bpy-free, so the drawing behaviour is tested with synthetic rays rather than
  simulated mouse events.
- GPU overlay: arcs by type, nodes by role, refused patches in red. Draw
  handlers never raise — missing region, missing graph and failed batch builds
  are early-returns.
- `New Layout` starts an empty layout pinned to the active mesh.
- **Fixed: every `poll()` raised `AttributeError` in restricted contexts.**
  `context.active_object` does not exist in a timer or handler; polls now go
  through `active_object()`. This spammed the console on redraws.
- `scripts/gui_check.sh` verifies the viewport half under xvfb: tool
  activation, modal poll, and overlay pixels via an offscreen render.
- 79 headless checks + 8 GUI checks.


## 0.1.0 — unreleased

First cut: the layout graph, the quantizer, patch fill, and the rebuild
pipeline. Authoring is bootstrapped from edge selection; the modal draw tool
and the suggestion lanes (SPEC §7) are not written yet.

- Layout graph (nodes / arcs / patches) stored as JSON on the object, with
  barycentric pins so a layout survives edits to the reference sculpt.
- Patch discovery by planar-graph face traversal. The rotation system around a
  node comes from a PCA of the incident arc directions, with the reference
  normal used only to fix its sign — a normal-based ordering is ambiguous on a
  sharp rim and fuses patches that should be separate.
- Corner detection on valence **or** turn angle. Valence alone treats the
  corner of a plain grid (degree 2, 90°) as mid-side and hands the filler a
  triangle where a quad belongs.
- Global integer quantizer. All patch constraints are derived at runtime from
  one small system rather than hard-coded per arity, so the familiar
  "opposite sides match" quad rule and the parity rules for 3-, 5- and 6-sided
  patches come out of the same place.
- Parity is solved over GF(2) in one pass, not hill-climbed. On a closed
  triangulated surface every arc is shared by two patches, so a local search
  stalls with most patches still odd — measured: 30 of 80 patches unsolvable
  by greedy, 0 after the linear solve.
- Per-arc floors: a single-arc side of a non-quad patch needs at least 2
  segments, and every pass respects that. Without it the sphere's pole fans
  come out unfilled at coarse densities.
- Fill: discrete Coons for quads, half-sum split templates for everything else,
  Laplacian relaxation and reprojection onto the reference. Boundary vertices
  are owned by arcs, so patches weld by construction — there is no distance
  merge anywhere in the pipeline.
- A patch that cannot be quantized, split or filled without going non-manifold
  is dropped and named in the report. It is never fudged into the mesh.
- 65 headless checks (`tests/run_all.sh`), green on Blender 5.2.0.

Verified closed and all-quad (χ=2, zero non-manifold, zero boundary, zero
surface deviation) across every density tested on icosphere, UV sphere and
cylinder layouts.

Known limits, honestly: a layout taken from *every* edge of a dense mesh (the
Suzanne case) produces large many-sided patches the filler refuses — that is
the automatic path this addon deliberately is not, and the manual workflow does
not hit it. The quantizer's repair is a heuristic, not the min-cost-flow ILP of
Campen/Bommes/Kobbelt 2015. There is no modal draw tool yet, no delta layer,
and no data transfer on Apply.
