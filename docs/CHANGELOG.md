# Changelog

## 0.13.0 — unreleased

**Ring bridging: a tube of clean quads in two swipes.** After a ring cut, the
next ring connects itself to the previous one with four straight wall arcs —
swipe down a limb and the ladder builds as you go. On by default (`Bridge
Rings`); wall arcs are straight-rail, so dragging a ring node re-lays them.

Two guards keep it honest:

- Node pairing must come out **bijective by proximity** — chain direction from
  a cross-section is arbitrary, so index order cannot be trusted.
- Rings farther apart than their own circumference are not a tube segment, so
  **ringing the left leg and then the right never bridges the gap** (measured:
  refused, 8 arcs, no walls).

**Symmetry stops duplicating roughly-mirrored geometry.** Two fixes found by
ringing both legs of a two-leg body with symmetry on:

- Twin adoption matched hand-drawn counterparts only within the *seam*
  tolerance (2 mm); real counterparts differ by centimetres, so sync mirrored
  fresh arcs **on top of the artist's own**. Adoption now uses a tolerance
  proportional to the arc, with a midpoint check.
- When no arc-to-arc correspondence exists at all (two rings anchored on
  opposite sides of their limbs are rotated half a ring apart), the would-be
  mirror is checked against existing authored geometry and **skipped when it
  already lies along it** — counts stay untied, which is pinnable, but nothing
  is doubled.

295 headless checks + 12 GUI checks; sweep 122/122.


## 0.12.0 — unreleased

**Ring Cut: swipe across a limb to ring it.** Ctrl+Alt drag across a leg, an
arm, a tail — the stroke and the view direction span a cutting plane, its
cross-section with the reference is chained into loops, and the loop under
your stroke becomes a closed ring of four even, welded arcs. One gesture
replaces clicking around the back of the mesh with the view rotated.

- On a body the plane cuts the torso behind the limb too; the loop **nearest
  the stroke** wins, so a swipe on the leg never grabs the torso.
- A natural swipe overshoots the silhouette on both ends; the stroke is
  sampled and clamped to what actually lands.
- The ring's first node is anchored under the stroke start, so **successive
  rings around the same limb get corresponding nodes** (measured 0.015
  lateral offset) — bridging them is four obvious click-click segments that
  snap.
- Rings are ordinary arcs: they mirror under symmetry, take arc types, pin,
  and erase like anything drawn.

290 headless checks + 12 GUI checks; sweep 122/122.


## 0.11.0 — unreleased

An audit pass over the recent work. Four families of defect, all found by
looking for the shapes of bugs already fixed once.

**Patch attributes are now keyed on canonical arcs.** Holes and per-patch
density were keyed on raw arc ids, which fails three ways at once under
symmetry: density set on a mirrored-side patch was silently dropped (its arcs
are never read by the solve — measured, zero effect); an attribute on one side
left the other bare, so a "symmetric" mesh came out asymmetric (0.667 mirror
error from one hole); and mirrored arc ids are regenerated every sync, so keys
could rot. Mapping arcs to their mirror source or twin first gives a patch and
its mirror one shared key: a hole on either side holes both (mirror error
0.000), density on either side densifies both, and with symmetry off nothing
changes.

**A loose point can be deleted.** Erase only knew how to dissolve valence-2
nodes or remove arcs, so an isolated point matched neither branch and the
click just died. Ctrl-click now deletes lone points, dangling stubs and whole
junctions with their arcs. Placing points without drawing arcs is also
undoable now — they were saved but never got an undo step.

**The overlay stops re-parsing the document every frame.** Both draw handlers
and the hover operator parsed the full layout JSON per viewport redraw and per
mouse move — 18.6 ms on an avatar-scale layout, most of a frame budget.
Read-only consumers now share one cached parse keyed on the stored blob:
0.25 ms. Writers still parse fresh, which is what lets a cancelled modal throw
its edits away. The face-count panel also ran the entire quantiser per redraw
to show the layout floor; cached the same way.

**Every dead gesture says why.** Thirteen operator paths returned CANCELLED in
silence — clicking with no reference set, aiming at an arc and missing. Each
now reports what went wrong.

280 headless checks + 12 GUI checks; the 122-layout sweep re-run clean.


## 0.10.2 — unreleased

**Fixed: pins on half the arcs did nothing.** Locks were read only from the
arcs that represent their group in the solve. With symmetry on, every mirrored
or twinned arc is represented by its partner — so a pin on one of those was
silently dropped and the solve carried on as if you had not asked. That is half
the arcs on a symmetric layout.

Locks are now collected from every arc and mapped onto its representative. A
pin on either half is honoured and its partner follows it.

Pinning two halves to *different* counts cannot hold, and is now reported as a
conflict in the panel rather than one of them quietly winning.

Also: the deferred rebuild no longer swallows exceptions. If it died, the pin
was stored and nothing re-solved — indistinguishable from the solver ignoring
you. (Verified separately that the timer does fire and does re-solve.)

266 headless checks + 12 GUI checks.


## 0.10.1 — unreleased

**Type the loop count instead of scrolling to it.** Alt+Shift click an arc to
select it — it is drawn in pink with its count shown — then type an exact
number in the Size panel. Ctrl+Wheel also selects whatever it adjusts, so an
overshoot can be corrected by typing rather than scrolling back.

**Unpin one arc.** `Unpin Arc` clears just the selected one; `Clear Loop Pins`
still clears everything.

**Fixed the overshoot itself.** Every wheel notch rebuilt the mesh
immediately, so on a heavy layout the wheel events queued up behind the
rebuilds and the count sailed past whatever was wanted. The pin now lands at
once and the mesh catches up ~0.25s after the wheel stops, coalescing a whole
burst into a single rebuild.

261 headless checks + 10 GUI checks.


## 0.10.0 — unreleased

**Per-patch density.** Ctrl+Alt+Wheel over a patch asks for more or less
resolution just there — detail in the face, less in the boots. This is the
authored counterpart to QuadForge's automatic curvature adaptivity: the artist
says where.

It needed no special case in the solver. A patch wanting more resolution is
expressed as longer arcs, and the quantiser already balances inconsistent
targets by least squares. Overrides are keyed on the patch's arcs, like holes,
so they survive re-discovery and density changes.

250 headless checks + 10 GUI checks.


## 0.9.5 — unreleased

**Loop counts you can grab.** Ctrl+Wheel over an arc pins how many loops cross
it. The global solve then keeps every patch closed around that, so pinning one
arc ripples through the rest of the model — measured, three other arcs re-solve
from a single pin.

The quantiser is the cleverest thing in this addon and it was invisible behind
a density slider. Pinned arcs are drawn in their own colour and their counts
are printed in the viewport, along with the count of whatever arc you are
hovering, so the solve is something you can reason about rather than trust.

`n_lock` has existed in the data model since 0.1.0 and the solver has always
honoured it; there was simply no way to reach it.

- A pin holds through a density change — that is what pinning means.
- Two pins that cannot both hold are reported as an unresolved patch, not
  silently dropped.
- `Clear Loop Pins` in the Size panel hands control back to the size settings.

242 headless checks + 10 GUI checks.


## 0.9.4 — unreleased

**n-sided patches unwrap as one island instead of shattering.** Their sub-
blocks are a quad fan meeting at a centre, and a fan of n quads cannot be laid
out rigidly on a lattice for n != 4 — which is why every pole fragmented into a
dozen islands. Dropping the rigid constraint *inside* a patch buys a single
seamless disc. A cylinder went from **33 islands to 3**.

Rim points follow the patch's **real proportions**, not a perfect circle. A
circle turns a thin triangle into a hexagon and stretches it badly — measured
9.8x texel spread on a cone's slant patches, now **2.2x**.

Island scale is now true 3D area over actual UV area, rather than over a count
of lattice cells. Counting cells only works while every cell is a unit square,
which a disc chart's wedges are not.

231 headless checks + 10 GUI checks.


## 0.9.3 — unreleased

**Hover highlighting.** With the Loom Draw tool active, whatever is under the
cursor lights up before you click — a node as a large amber dot, an arc along
its whole length. You can see what Ctrl or Shift is about to grab instead of
finding out afterwards.

This was only affordable once surfaces were cached: it runs from mouse-move,
costs 0.23 ms per move, is throttled to 2 pixels, and only asks for a redraw
when the highlighted thing actually changes.

Verified by rendering the overlay offscreen and counting pixels: 0 amber idle,
225 hovering a node, 576 hovering an arc.

224 headless checks + 10 GUI checks.


## 0.9.2 — unreleased

Three things from drawing on a real model.

**Moving a node wrecked the arc.** It rewrote only the polyline's endpoint and
left every interior sample where it was, so the arc got a spike at the node
instead of following it. How an arc follows now depends on how it was made:

- A **clicked segment** has no shape of its own — it was derived from where its
  endpoints were — so it is re-laid end to end and the whole arc moves.
- A **freehand stroke** *is* the artist's line, so it bends with a smooth
  falloff rather than being thrown away. `Bend` controls how much of it
  responds.

**Clicking felt unresponsive because it was.** Every click that erased an arc,
dragged a node or toggled a hole rebuilt the entire BVH over the reference —
86 ms on a 20k-vertex mesh, far worse on a character — and so did every refresh
after drawing. Surfaces are cached now: **86 ms → 0.08 ms**, rebuilt only when
the reference actually changes.

The cache is keyed on datablock pointers, not geometry alone. Keyed on geometry
it matched a *freed* object after a file load and handed back a Surface built
over dead data. A cached Surface also no longer holds a reference to its
object at all.

**Grabbing is more forgiving.** `Pick` (26 px) is separate from `Snap` (18 px):
snapping while drawing should be conservative, but grabbing something should
not be.

222 headless checks + 8 GUI checks.


## 0.9.1 — unreleased

Two regressions reported from drawing on a real model.

**Placing a point deleted it.** A node with no arcs is the point you just
placed. Symmetry sync swept every orphan node on each refresh, so the anchor
was gone before the second click and point-first authoring was impossible —
including with symmetry switched off, since the sweep ran either way. Only
derived nodes are swept now.

**Dragging was unusable on a dense mesh.** Two compounding causes:

- `ray_hits` recomputed the model's bounding span from every vertex **on every
  ray**, so tracing scaled with the reference's vertex count.
- The modal re-traced the *entire* stroke on every mouse-move, which is
  quadratic in stroke length.

Together: 9.7 ms per mouse-move on an 8k-vertex sphere, worse as either grew.
The span is cached on the Surface, and a drag now traces only its newest
sample. Measured **0.01–0.02 ms per mouse-move at 32k vertices** — roughly a
thousand times faster and no longer sensitive to mesh density.

213 headless checks + 8 GUI checks.


## 0.9.0 — unreleased

**Layout retargeting.** Move a layout from one mesh onto another, topology and
all. Draw your face topology once and drop it on every avatar you own.

A retopologised mesh is vertices — positions on one specific surface, and
meaningless anywhere else. A layout is intent, and intent transfers. A 3D
thin-plate spline is fitted to landmark pairs, every node and arc sample is
warped through it, then projected onto the target and re-pinned. The topology
is untouched by construction, so holes, seams, arc types and locked counts all
survive the move.

Landmarks come from whatever the two models already agree on:

- **Matching bone names.** Two humanoid rigs already share them, which is a
  dense anatomical correspondence sitting right there — far better than
  anything inferable from the shapes alone.
- **Matching empties** parented to each mesh.
- **Bounding box corners**, which know nothing about anatomy and are a starting
  point to edit rather than an answer.

Under four landmarks the fit degrades to a least-squares similarity rather than
producing nonsense.

Measured on a sphere retargeted onto an offset, squashed sphere: topology
identical (266 nodes, 552 arcs, 288 patches), every node re-pinned, holes and
seams preserved, and the rebuilt mesh lands on the target with 0.0000 max
deviation.

206 headless checks + 8 GUI checks.


## 0.8.0 — unreleased

Two things the layout-as-document model gets almost for free.

**LOD sets from one layout.** `Make LODs` re-solves the same layout at smaller
budgets. The patch structure is untouched, so every level is the same surface
at a different resolution — UVs, seams, materials, weights and shape keys all
match across levels, from the same source. Verified: weights still correlate
1.000 with the source gradient at the coarsest level.

A layout also now reports its **structural face floor**. N patches cost faces
no matter what, so asking for fewer is a request the layout cannot represent.
LOD emission stops there and says so rather than emitting identical levels, and
the Size panel warns when a budget is below it.

**UVs straight from the layout.** `UVs from Layout` unwraps with nothing
inferred — a quad patch *is* a `p x q` grid. Adjacent patches merge into one
island by propagating a rigid lattice transform across shared arcs, which works
only because the quantiser guarantees matching counts there. Arcs typed `seam`
stop the merge; a surface that closes on itself gets cut where the walk meets
itself.

- Texel density measured **1.000x on a uniform layout**, 1.5x on a drawn
  sphere, and every face lands inside 0..1 with no degenerate UVs.
- A doubly-closed surface (torus) unwraps without folding over itself.
- A seam stops islands *merging*; it does not force a *split*. A cut through
  the middle of a flat sheet leaves it connected round the ends and nothing
  needs to open — ringing a patch does separate it.

Deriving island area from the patch rather than from the faces was wrong for
n-sided patches, whose sub-blocks each got credited with the whole patch's
area and came out ~n times too large — 16x-25x texel spread before the fix.

190 headless checks + 8 GUI checks.


## 0.7.0 — unreleased

Mirrored hand edits, a face budget, and a usability pass.

**Mirror Hand Edits** (toggle, off by default). Captured vertex edits are
copied across the symmetry plane, so an edit made on *either* half propagates
instead of the generated side silently losing it. A vertex on the seam is its
own partner, so its displacement along the mirror axis is dropped — any other
value would push the seam off the plane.

**Face budget.** `Size By: Face Count` targets a total face count instead of an
edge length, which is how game-asset budgets are actually specified. The
quantiser already predicts the count without filling anything, so the edge
length is solved by bisection over an exact estimate. An exact hit is often
impossible — subdivisions are whole numbers — so the closest reachable count is
used and the panel reports how far off budget it landed.

**Fixed: two sidebar buttons could not work.** `Toggle Hole` and `Draw Arc` are
invoke-operators that raycast from the mouse position; pressed from the
sidebar, the cursor is over the sidebar, so they picked nothing. They live in
the tool keymap now, and the panel points at the tool instead of pretending to
be it.

**Fixed: Apply left bookkeeping behind** — the `nx_loom_patch` face attribute
and the background/problem caches survived onto a mesh that is meant to be
plain.

Also:

- Generated objects draw **in front**, so they stop z-fighting the surface they
  sit on the moment you make one.
- The panel is collapsible sub-panels rather than seven stacked boxes.
- The reference shown is the one the *layout* uses, not the scene fallback, and
  says when it is falling back.
- `Density` is now honestly labelled **Edge Length** — it is a length, and
  turning it up makes the mesh coarser.
- `Show Problem Patch` moves the view to the first unresolved patch instead of
  leaving you to hunt for it. `Hide Reference` toggles the sculpt.
- `clean_build` now symmetrises exactly as `rebuild` does. It did not, so
  capturing edits with symmetry on recorded the symmetrisation itself as if it
  were a hand edit.

164 headless checks + 8 GUI checks.


## 0.6.0 — unreleased

Symmetry, done at the layout rather than at the mesh. Draw one half, get both;
the seam is welded by construction because the two halves share the nodes on
the plane.

- `Symmetry` axis (X / Y / Z) with a seam tolerance. Near-plane nodes snap onto
  the plane and are shared; arcs crossing it are split there.
- Counts are quantised over **representative** arcs, so both halves get
  identical subdivisions. Symmetrising after an independent solve does not
  work — the halves drift.
- Generated positions are forced into exact mirror pairs. The layout mirrors
  exactly but reprojection does not: the reference's own triangulation is
  asymmetric and pulled the halves ~2e-2 apart. Now **bit-exact (0.0) at every
  density**.
- Deleting an authored arc removes its mirror for free; turning symmetry off
  leaves exactly what was authored.

Two bugs found while building it:

- Adopting a hand-drawn counterpart by marking it `mirror_of` made the artist's
  own geometry **derived**, so the next sync deleted and regenerated it —
  churning arc ids and breaking every hole key and delta keyed to them.
  Authored pairs use a separate `twin` field that is never deleted.
- One-to-one pairing matters: a per-vertex nearest search let two vertices
  claim the same partner, leaving a third unpaired and quietly asymmetric.

Schema is now v2 (`mirror_of`, `twin`); v1 layouts load unchanged.

140 headless checks + 8 GUI checks.


## 0.5.0 — unreleased

Three bugs reported from actually drawing on a model, and the behaviour that
replaced them.

- **Placing a point crashed.** A node with no arcs yet has no rotation system,
  and asking for one raised `IndexError` on an empty direction array — so the
  very first click of a point-first workflow errored. Lone points and dangling
  arcs are supported states now.
- **A ring round a limb produced nothing, then covered the hull.** Two separate
  causes. A smooth closed loop has no junctions and no sharp turns, so it had
  no corners and every cycle was rejected; such a loop is now cut into four
  sides at evenly spaced nodes. And a closed loop on a closed surface bounds
  *two* valid regions — the limb, and the entire rest of the model — so the
  leftover is detected as **background** and left alone rather than filled.
  (`fill_background` overrides.)
- **Marking holes.** Ctrl-Shift-click toggles a patch between filled and a
  hole, for eye sockets, mouth openings, anywhere geometry should not be. Holes
  are keyed on the patch's set of arc ids, not its id, so they survive
  re-discovery and density changes. Faces carry an `nx_loom_patch` attribute so
  a click can name a patch directly.
- **Strokes stay on the surface you can see.** A ray through a limb crosses the
  near wall, the far wall and whatever is behind it; the nearest hit is taken,
  with a deeper crossing used only when the nearest would tear the stroke.
  Selecting by shortest total path was tried and is wrong — a flat wall behind
  the model beats curving round the limb in front of it.

128 headless checks + 8 GUI checks.


## 0.4.0 — unreleased

Apply carries your data over, and three solver robustness fixes found by
sweeping 122 primitive layouts rather than by reading code.

- **Data transfer on Apply**: UVs, materials, vertex groups, shape keys,
  creases and bevel weights re-projected from the reference onto the new
  topology. QuadForge's `core/transfer.py`, vendored verbatim — see
  `nx_loom/core/vendor/PROVENANCE.md`. Opt-out, and never fatal.
  Measured: weights still correlate 0.996 with the source gradient.
- **Rotation system now uses the smooth vertex normal.** A PCA of the incident
  arc directions assumes they are coplanar. That holds at a cylinder rim, but
  at a cone's base the slant arc has a radial component and the three
  directions span all of 3-space, so the plane was arbitrary: discovery found
  3 cycles instead of 9 and **every cone was refused outright**. Cones now
  resolve to n triangles plus a base n-gon.
- **The relaxation is floor-aware.** Solving from raw targets and clamping to
  the floors afterwards leaves a globally inconsistent starting point — a pole
  fan pinned at 2 surrounded by arcs at 1 — which local repair cannot walk
  back. This left holes at the poles of coarse UV spheres.
- **Quantizing restarts from several roundings** when greedy repair stalls in a
  local minimum. Deterministic, and 22 of 122 swept layouts need it.

Sweep: 122 layouts across spheres, icospheres, cylinders, cones and tori at
three densities each — 0 with unresolved or broken patches, up from 10 failures
and 9 outright refusals.

108 headless checks + 8 GUI checks.


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
