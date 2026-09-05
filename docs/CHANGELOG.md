# Changelog

## 0.31.0

**The Assist slider, and combing the field.** One dial for how eager the
tool is, and a brush for telling it where the flow should run.

- **Assist** (main panel, 0..1, default 0.5). Scales how much the tool
  offers: how many arcs Suggest proposes (12 at zero, up to 84 at one, with
  matching length and spacing gates) and how hard and far the magnet pulls.
  Two rules keep it honest: **0.5 reproduces the pre-slider behaviour
  exactly** — a default must not change what you already know — and the
  slider only scales assists that have no knob of their own; explicit
  toggles (Bridge Rings, Magnet, Seam Snap) always outrank it. It never
  makes anything happen unprompted.
- **Comb Field** (Suggest panel, or **D** in the tool). Drag strokes where
  you want the flow to run. A comb stroke is a *hint*, not geometry: it
  soft-pins the suggestion field — outweighing the surface's own curvature
  opinion, yielding to authored arcs, drawn thin in the arc family's violet.
  Comb, then Suggest; the proposals follow your comb. Clear Combs resets
  the field to pure curvature. Under test: hints visibly bend the field
  where they run, and an authored arc on the same face always wins.

Suite: 482 headless + 19 GUI checks, 117-layout sweep clean.

## 0.30.0

**The competitive gaps** — the three things rival tools genuinely had on us,
plus radial symmetry because it sounds cool.

- **Constrained auto-complete** (vs ZRemesher guides). Suggest Arcs now
  treats your authored arcs as *constraints*: their tangents hard-pin the
  cross field (a pinned face is a constraint, not an opinion — a soft anchor
  loses the vote against three disagreeing neighbours), poles buried in
  authored geometry spawn nothing, and traces END when they reach an arc so
  accepting connects them. Draw the key lines; ask for the rest; the
  proposals flow into yours. A fully authored layout gets no proposals —
  complete the layout, never redraw it.
- **The result brush** (vs TopoGun). Press **B** (or the Edits panel
  button): drag tweaks vertices with soft falloff, Shift-drag relaxes,
  wheel sizes the brush. Every sample is glued back onto the reference so
  the mesh slides along the sculpt, and ending the brush captures the whole
  session into the delta layer — the massage survives every rebuild.
- **Map baking** (vs 3D Coat). *Bake Maps* in Finish: tangent normal (and
  optional AO) from the sculpt onto the layout UVs — generated first if
  missing — with the cage extrusion computed from the real gap between the
  meshes, images packed into the .blend, and every scene setting it touched
  restored. The pipeline actually ends inside the addon now.
- **Radialize.** Draw one wedge, set N and axis, and the rotational copies
  arrive as ghosts — the accept lane's snapping and crossing machinery
  welds the wedge borders, so there is no separate weld pass to get wrong.
  Copies that already exist are skipped: radialize twice, get nothing new.

Also fixed in passing, found by the sweep: a NaN could crash the quantizer
("cannot convert float NaN to integer") instead of degrading — the
least-squares boundary now falls back to plain targets on any non-finite
value, and a corrupted arc length is named in the console rather than
detonating forty frames downstream.

Suite: 474 headless + 19 GUI checks, primitive sweep clean (117 layouts —
the sweep script was recreated this release; the historic count was 122).

## 0.29.0

**Magnet drawing and the quality heatmap** — the two held-back ideas from
the veteran brainstorm.

- **Magnet drawing.** Toggle *Magnet* in the panel or press **M** mid-draw:
  freehand strokes snap to the sculpt's own ridges and valleys — an ear rim,
  a lip line, a hard-surface edge — instead of you re-tracing them by hand.
  The crease lines come from the same Cohen-Steiner/Morvan edge tensors the
  suggestion field anchors to (here the tensor's *axis* matters, not its
  4-RoSy class: a crease face's bending edges ARE the crease, so the dominant
  eigenvector runs along it); chains of decisive faces are walked into
  polylines, shown as faint icy guides while the magnet is armed. The pull
  attracts but never overrides the hand (max 85%, fading with distance) —
  SPEC §7 in miniature. Honesty gate: a sphere or a flat sheet offers the
  magnet **nothing** — decisive creases must stand far above the surface's
  background bend (median over every face), so featureless surfaces produce
  no guides rather than noise.
- **Quality heatmap.** *Quality Heatmap* in the Display panel colours every
  generated quad by the WORSE of its two sins — stretch (shortest/longest
  edge) and shear (corners off 90°) — warm meaning wrong, per the palette
  rule. A square scores 1, a 3:1 rectangle a third, garbage the bottom.
  See the pinched pole fans and sheared transitions before you Apply, not
  after you subdivide.

Suite: 451 headless + 19 GUI checks (heatmap under pixel test: sheared quads
render warm), 122-layout sweep clean.

## 0.28.0

**The veteran pass** — the three things an artist ten years into a tool like
this actually leans on: a personal topology library, the power to declare
work *done*, and control over where loops sit, not just how many there are.

- **Topology stamps.** The eye ring, the mouth loops, the pole fan you have
  drawn a hundred times — now a library. Three built-ins ship (eye, mouth,
  pole fan), and *Save Stamp* captures the arcs around the 3D cursor from any
  layout into your own library (flattened to a unit disc; curvature is
  intent, and placing re-projects onto whatever surface it lands on).
  Placing is a gesture: aim with the mouse, wheel to size, R to rotate,
  click to drop. A stamp lands as **suggestion ghosts** — the same
  accept/discard lane as the field suggestions, so a stamp is never applied
  on its own (SPEC §7) and inherits snapping, crossings and mirroring on
  accept. Stamp one eye with symmetry on; accept; both eyes.
- **Frozen regions.** Press **F** over a patch (or *Freeze All Solved*) to
  mark it done: its loop counts are pinned wholesale and no later re-solve —
  density change, face budget, edits elsewhere — will touch them. Cool mint
  wash + legend entry. Thaw per-patch with F or all at once. Keyed
  canonically like holes, so a mirrored pair freezes as one; editing a
  frozen patch's arcs changes its key and naturally thaws it — edited is no
  longer done. A hand pin on a shared arc still outranks the freeze.
- **Spacing bias.** Per-arc slider (Loops panel, on the selected arc):
  loops crowd toward one end of the arc — rows pinched into a knee or elbow
  crease — without changing a single count, so the solve is untouched and
  same-count hand edits stay lossless. Subdivision ticks preview the pinch
  live; mirrored arcs inherit their source's bias.

Suite: 440 headless + 18 GUI checks (frozen wash and stamp-preview renders
now under pixel test), 122-layout sweep clean.

## 0.27.0

**Progress, Cancel, and the end of the freeze** (user request, plus a field
report of Blender freezing outright mid-calculation).

Heavy work — rebuilds, Suggest Arcs — is now written as *progress generators*:
small units of work with honest `(fraction, label)` checkpoints between them,
and every mutation of the document (mesh write, graph store, ghosts) strictly
after the last checkpoint. That one shape buys all three features:

- **Progress bar.** When a rebuild or suggest is expected to be slow (the
  last one took >0.4 s), it runs as a background job pumped from a timer in
  ~80 ms slices: the UI stays alive, the sidebar shows a live bar
  ("filling patch 34/80", "tracing flow 3/12"), the status bar and mouse
  cursor track the percentage.
- **Cancel button.** Next to the bar, plus Esc-free: it works because cancel
  is honoured between work units — and it is *always safe*, since nothing has
  been written yet. A cancelled job leaves mesh, layout and ghosts exactly as
  they were.
- **The watchdog.** `Auto-cancel After` (Size panel, default 60 s, 0 = off)
  bounds every calculation — background *and* synchronous, Apply's capture
  included. A runaway solve raises out of the pipeline instead of freezing
  Blender; the panel then says the mesh is stale and why. The quantizer
  degrades gracefully under the budget: extra multi-start restarts are
  skipped, but the first solve and the seed rescue always run — the
  "topology-preserving edits never un-solve" guarantee does not depend on
  the clock.

Under test (test_23): chunked build is bit-identical to the blocking build;
progress is monotonic and labelled; an expired budget raises; a timed-out
rebuild returns None, names the reason, and leaves the document untouched;
the pump completes/cancels/watchdogs correctly; a cancelled suggest job
proposes nothing. Suite: 423 headless + 16 GUI, 122-layout sweep clean.

## 0.26.1

**The suggestion lane survives contact with a real avatar.** Field report from
an NX-Dinasty session: trace spaghetti knotted between the toes, meandering
lines wandering the torso, and — with symmetry on — proposals that ignored the
mirror entirely.

- **Tracing is connectivity-constrained now.** The tracer walks the actual
  face adjacency of the proxy instead of snapping to the globally nearest
  face, so a trace can never teleport across the gap between two toes (or any
  two disconnected shells). If it loses the surface, it ends — under test with
  twin icospheres: 22 traces, 0 hops.
- **The proxy is Taubin-smoothed before the field solve**, singularities are
  clustered before seeding, short scraps are dropped, and traces stop at 80%
  of the sculpt's span instead of wandering forever. High-detail regions stop
  breeding singularity storms.
- **Symmetry-aware suggesting.** With a mirror axis set, the field is solved
  on the kept half only, ghosts are drawn mirrored so you see the real
  outcome, and Accept commits through the standard seam machinery — accepted
  arcs come out paired, zero unpaired. Proposals hugging the seam band are
  clipped away: that line belongs to the seam curve, and its near-coincident
  mirror image only bred unpaired-arc warnings.
- The mirror-coverage guard's reach is now capped at ~2% of the layout span.
  It exists to avoid doubling roughly-mirrored *hand-drawn* geometry; the
  uncapped fraction-of-length reach let long arcs claim coverage over clearly
  separate parallels.

Suite: 411 headless + 16 GUI checks, 122-layout sweep clean.

## 0.26.0

**The organic suggestion lane** — promised since 0.1.0, and the lane chosen
first back when the project began.

`Suggest Arcs` smooths a 4-RoSy cross field over your sculpt, anchored to
principal curvature where the surface is decisively bent; the field's
singularities mark where poles belong, and the separatrices running out of
them are exactly the arcs a retopologist would draw. They appear as **ghost
polylines** — visibly not part of the document — with Accept and Discard
beside them. Accepted proposals go through the same commit machinery as a
hand-drawn stroke (snapping, crossings, seam handling), and become ordinary
arcs with no memory of where they came from.

SPEC §7 discipline, under test: nothing runs unprompted, ghosts touch no
geometry, a featureless surface yields **no** proposals rather than noise, and
discard removes exactly the ghosts. Field honesty, also under test: a
cylinder's field runs along axis and rings with zero invented poles, and a
sphere's field carries the eight quarter-turns of index that topology owes.

Big sculpts are decimated to a solving proxy automatically; the field itself
is vectorised (~360 ms at 7.7k faces).

405 headless checks + 16 GUI checks; sweep 122/122.


## 0.25.0 — unreleased

**The mirrored half stops being a trap.** "Doing stuff near the symmetry line
can be so annoying" — reported, and warranted. Three traps removed:

- **No more zombie arcs.** Erasing a mirrored arc used to bring it straight
  back on the next sync. Every destructive gesture on derived geometry now
  redirects to its authored source — erase, dissolve, merge, retype, select —
  so acting on either half is acting on the document, and things you delete
  stay deleted.
- **No more reverting drags.** Moving a mirrored node used to hold only until
  some later edit regenerated the mirror and silently threw your move away.
  Dragging the mirrored half now drives the authored source with the
  reflected position — your side follows live, and the change is permanent.
- **Drawing works on either half.** A stroke attached to mirrored geometry
  used to decay into orphaned stubs that fought the next sync. It is now
  reflected wholesale and committed on the authored side; sync mirrors it
  back to exactly where your hand drew it. Nothing ends up unpaired.

**And the seam snap learned manners.** `Snap to Seam` can be turned off in the
Symmetry panel, and holding **Ctrl while clicking** in the draw tool bypasses
it for that click — so you can finally place geometry deliberately close to
the middle without being yanked onto it.

395 headless checks + 16 GUI checks; sweep 122/122.


## 0.24.0 — unreleased

A full audit — hunting by the bug families this project has actually produced.
Three real finds, all fixed and pinned by regression tests:

- **A pin on a mirrored arc vanished when its source was edited.** Sync
  regenerates a mirror whose source changed — and the regenerated arc came
  back with a fresh id and no pin, silently taking the artist's loop count
  (and any hand-edit provenance keyed to that arc) with it. Regenerated
  mirrors now come back with the **same identity**: same arc id, same node
  ids, same pin.
- **Capturing right after an edit recorded ~30 phantom hand edits.** The
  rebuild after an edit built from a not-yet-converged state (seam pin
  round-trips settle over about two cycles), so the next clean comparison saw
  boundaries ~1e-4 away and refilled those patches differently. The rebuild
  pipeline now converges positions to their fixed point before building —
  nearly free, since sync signature-skips once converged. One edited vertex
  captures one delta again.
- **The 1–4 hotkeys ate Blender's collection-visibility keys** even with no
  layout anywhere. They now yield unless a layout is active. Checkpoint
  delete gained its missing guard, and the overlay cache key was hardened
  from a prefix comparison to a full content hash.

385 headless checks + 16 GUI checks; sweep 122/122.


## 0.23.1 — unreleased

**Fixed: the whole overlay rendered at back-side strength.** The 0.21 depth
fade tests arcs against the depth buffer — but the arcs lie exactly ON the
surfaces in that buffer (the sculpt's skin, and the generated mesh's own patch
borders once the reference is hidden), so the depth test was a coin flip and
over half the *front* of the layout drew at the faint behind-alpha. Measured:
676 of 1546 front-arc pixels survived; with the overlay pulled toward the
camera by a small NDC bias, all of them do. Both passes share the bias, so the
front/back split stays consistent.

Also a general visibility bump: full-alpha flow arcs, thicker lines, larger
nodes, the behind-pass at 0.35 instead of 0.25, and line/point sizes now scale
with the system UI scale on hi-dpi displays.

The test rig learned to lay real depth into its offscreen buffer first —
without that, every depth-tested pass wins trivially and occlusion bugs are
invisible to it. This one shipped because the harness could not see it.

381 headless checks + 16 GUI checks; sweep 122/122.


## 0.23.0 — unreleased

Usability pass, final slice.

- **Checkpoints.** Save the whole layout under a name before trying something
  drastic; restore or delete from the sidebar. Cheaper than undo-scrubbing —
  the document is one blob, so a named state costs nothing.
- **Slide (Ctrl while dragging a node).** Constrains the drag to the node's
  own arcs, so a node moves *along* its line instead of freely over the
  surface. The rails are frozen at drag start — the live paths deform under
  the node, and a rail must not chase its own tail.

Deliberately deferred, with reasons: **background rebuild** (threading through
an otherwise-stable pipeline for edits that already cost ~0.3–0.5 s — worth it
only if large-layout lag comes back) and the **brand palette deep pass**
(cosmetic-last; the palette got its correctness fix in 0.21).

381 headless checks + 15 GUI checks; sweep 122/122.


## 0.22.0 — unreleased

Usability pass slice 3: failures explain themselves.

- **Per-patch diagnosis.** A failing patch now says why, in numbers: "opposite
  sides disagree: 3 vs 2 vs 9 vs 2 — pinned arcs 20 (3*), 19 (9*) force it",
  "5-sided patch needs an even loop total, it gets 9", "side counts cannot fan
  around a centre — add density here". Shown in the panel and echoed by Show
  Problem Patch.
- **Fix It.** One click applies the first repair that actually works — every
  candidate is validated on a copy of the document first, and a "fix" that
  fails or breaks something new is never applied. Releasing the pin that
  fights its own target hardest comes first; raising density inside a
  starved patch is the fallback.
- **Typing an impossible pin warns immediately**, in the Loops panel, instead
  of leaving a silent red patch to find.
- **Apply confirms over failures.** Unresolved patches become permanent holes
  on Apply; that now asks, instead of rewarding a habit-click.
- **Collapsed slivers are counted.** A merge that produces a two-sided region
  used to vanish silently from the fill; the panel now says so.

374 headless checks + 15 GUI checks; sweep 122/122.


## 0.21.0 — unreleased

Usability pass slice 2: the visual instrument.

- **State fills.** Failing patches wash red, the background region dims grey,
  holes get their own outline — states visible at a glance instead of
  inferred from arc outlines.
- **Subdivision ticks.** Dots along every arc where vertices will land, so
  density is visible *before* a rebuild.
- **Colour legend.** A small key in the viewport corner — every overlay
  colour finally explains itself. Toggleable, like the rest.
- **Depth-faded X-ray.** The layout behind the model renders at quarter
  strength instead of shouting through at full; the front stops visually
  fighting the back.
- **The seam is drawn.** With symmetry on, the mirror plane's trace across
  the reference is visible even where no arcs run.
- **Palette fix:** crease arcs were nearly the same orange as the unpaired
  warning — the one alarm colour was ambiguous. Creases are now hot lavender;
  warm tones (orange, red, amber) belong to states exclusively.

359 headless checks + 15 GUI checks (red wash, ticks and legend verified by
pixels); sweep 122/122.


## 0.20.0 — unreleased

The first slice of the big usability pass: modeling speed.

**Loop cut (C).** Hover an arc, press C, and a loop parallel to it previews
through the entire quad strip — click to insert it. The layout version knows
what a mesh knife cannot: which side of each patch is opposite, so the cut
runs through each patch's own transfinite parameterisation and lands
proportionally in curved or tapered patches; and where the strip honestly
ends — it stops at poles, holes and boundaries, and closes into a ring when
the strip wraps around. Committing reuses the crossing machinery, so every
shared side it passes gets a proper junction.

**Repeat ring (R).** After two ring cuts, R drops the next ring at the same
spacing along the limb, bridged like the rest. A ladder down a leg: swipe,
swipe, tap tap tap. Verified: rings at −1.0, −0.6 extrapolate to exactly
−0.2.

**Arc-type hotkeys (1–4).** Flow, crease, boundary, seam while drawing — no
more dropdown round-trips.

359 headless checks + 12 GUI checks; sweep 122/122.


## 0.19.0 — unreleased

**A line drawn through another line connects to it.** Crossing an existing arc
now splits both at a shared junction, exactly as ending on one always has —
click-segments and freehand strokes alike, with multiple crossings handled in
order along the stroke. A crossing that lands near an existing node reuses it
instead of stacking a near-duplicate.

This was a correctness hole, not just a convenience: a line floating over
another makes the layout non-planar, and patch discovery silently
mis-traverses it. Verified on the reported case — a diagonal drawn
corner-to-corner through a region's middle arc now meets it at a valence-4
junction and the region builds clean quads.

349 headless checks + 12 GUI checks; sweep 122/122.


## 0.18.0 — unreleased

**A fully mirrored layout can no longer fail on one side.** Reported with a
screenshot: "27 mirrored, 0 twinned", one cheek red, its mirror fine — which
the 0.16 diagnosis said was impossible for paired regions. The leak was patch
**discovery**: mirrored arcs are exact copies, but patches were re-derived per
side using surface normals from the (asymmetrically triangulated) sculpt and a
50° corner threshold, so a borderline corner call could flip on one side only
— two exactly-mirrored regions, different patch structures, different
constraints, one solvable.

Mirrored-side patches are now **constructed as mirror images of the authored
side's decomposition** instead of rediscovered — orientation flipped, corners
remapped, fill flags carried. Regions drawn by hand on both sides (twins) keep
their own discovery. Under test: an unsolvable region now goes red on *both*
sides, every mirrored patch shares its source's canonical key (holes and
density line up by construction), and a corrupted mirrored decomposition is
reconstructed.

**Choosing which side to mirror is point-and-shoot now.** "Keep +/−" required
knowing which side of the axis you were looking at. Alt+Shift click an arc on
the side you like, then **Mirror From Selected Side** — and the Exact Mirror
scope (replace twinned counterparts' shapes too) is finally reachable from the
panel; before, it existed only through the operator's hidden options. The
panel also warns when twinned pairs have drifted apart in shape: counts tied,
geometry not mirrored.

342 headless checks + 12 GUI checks; sweep 122/122.


## 0.17.0 — unreleased

**Merge by dropping.** Shift-drag a node onto another and they weld: the
target highlights in amber while you hover it, the dragged node's arcs
re-anchor (straight segments re-lay, freehand strokes bend, exactly as any
drag does), a direct connector between the two collapses, and locks and arc
types survive the move. A merge target outranks the seam snap — welding is
the more specific intent.

Two surviving arcs between the same pair of nodes are deliberately left
alone: two different routes between the same nodes is legitimate geometry —
the two halves of a ring are exactly that.

337 headless checks + 12 GUI checks; sweep 122/122.


## 0.16.1 — unreleased

**Nudging a vertex can no longer un-solve the layout.** Reported: push a node
up, patches go red; pull it back, they solve. That was a bug, and a specific
one — whether a layout can solve is purely topological (parity, locks,
floors), and moving a node changes none of it. It only changes the arc
lengths the heuristic starts from, and the repair phase could stall from one
starting point while succeeding from another.

The proof it was a bug is also the fix: the counts that solved before the
nudge are still a complete valid solution after it. The solver is now seeded
with the last successful counts (they ride on the arcs already), and when a
fresh solve stalls, settling from the seed recovers them. Failed solves are
also no longer cached, so a later attempt with a better seed gets its chance.

Tested both ways: a deliberately cliffed fresh solve is rescued by the seed
alone, and dragging a node through twelve random positions on a pinned layout
never fails to solve.

330 headless checks + 12 GUI checks; sweep 122/122.


## 0.16.0 — unreleased

**Why one side of a "mirrored" layout can fail to solve while the other is
fine.** A genuinely paired region cannot do that — partners share one
subdivision count and solve or fail together. When it happens, that region is
not actually paired: both sides carry hand-drawn geometry too different to
twin, so the two halves quantise **independently**, and slightly different
structure on one side can be unsolvable where the other side's is fine.

The tool now shows and fixes this instead of leaving it to be deduced:

- **Unpaired arcs draw in warning orange** whenever symmetry is on, and the
  Symmetry panel counts them with an explanation.
- **Make Truly Mirrored** (Keep + / Keep −) drops the unpaired arcs on one
  side and regenerates exact mirrors of the kept side. Verified: 14 unpaired
  → 0, mesh exactly symmetric again (0.0 mirror error).
- An **Exact Mirror** scope also replaces *twinned* counterparts — pairs whose
  counts are tied but whose hand-drawn shapes differ — for full geometric
  symmetry, at the cost of the discarded side's shapes.
- New invariant under test: paired arcs always carry identical counts.

324 headless checks + 12 GUI checks; sweep 122/122.


## 0.15.0 — unreleased

You were right: the more you added, the more got recalculated. An edit on a
552-arc layout cost **8.5 seconds**; it is now **0.5 s**, and a rebuild where
nothing changed is 0.3 s.

Profiled, then fixed in order of what the profile said:

- **Sync was 94% of an edit and quadratic.** It regenerated every mirrored arc
  from scratch every time, with Python-loop pairwise scans. Now: a content
  signature skips sync entirely when the authored half is unchanged (4 ms); a
  mirror whose source arc did not change is kept rather than regenerated;
  twin adoption and endpoint matching are vectorised; coverage checks query a
  KD-tree instead of scanning every arc. 7.9 s → 58 ms.
- **Pairing verts for exact symmetry was O(V²)** — a full pairwise matrix,
  25M distances on a 5k-vert half. Spatial grid now.
- **Patch filling re-ran for every patch on every edit.** Fill results are
  memoised on the patch's boundary geometry, so only patches an edit actually
  touched refill. The count solve is memoised the same way.
- **The seam oscillates by pin round-trips (~1e-4)** between syncs, so every
  cache key that rounded tighter than that never matched. Keys round at 1e-4
  world units — far below anything a hand does on purpose.

**Correction to 0.13.0's notes:** the "adoption tolerance proportional to the
arc" described there never actually shipped — the patch silently failed to
apply, and only the duplication guard went out. It is genuinely in now,
vectorised, matching by *best* candidate rather than first (index order could
steal an exact counterpart's pairing on dense layouts), and comparing true
arc-length midpoints — the sample-index "midpoint" of a two-point arc is its
endpoint, which refused every reversed exact pair.

**Seam snap.** Aiming for the middle by eye is over: with symmetry on, a
click, stroke end, or node drag within snap range of the plane lands *exactly*
on it, a cyan marker with a "mid" tag shows before you commit, and sync then
shares the node between both halves instead of mirroring a near-duplicate.

315 headless checks + 12 GUI checks; sweep 122/122.


## 0.14.0 — unreleased

Face work and line quality.

**Halo: the eye-socket gesture.** Ctrl+Alt+Shift drag outward from a point —
the drag sizes a ring around it, snapped to the surface, with the first node
placed where you release. Two concentric halos bridge into a loop band, so an
eye socket is: halo, halo, Ctrl+Shift-click the middle to hole it. Three
gestures, verified end to end (clean quad band, open socket).

**Stroke smoothing.** A wobbly freehand stroke becomes a wobbly edge loop in
every mesh generated from it forever, so hand jitter is faired out at commit —
a low-pass filter, not a straightener (measured: ~90% roughness removed while
landing *closer* to the intended curve, endpoints untouched, still on the
surface). `Stroke Smoothing` in the Display panel, 0 keeps every wobble.
**Smooth Arcs** fairs existing arcs retroactively — the selected one, or every
freehand arc.

Also: the deferred rebuild queue now holds the object's *name*, never a bpy
reference — a reference kept across frames dies with a ReferenceError if the
object is deleted before the timer fires.

308 headless checks + 12 GUI checks; sweep 122/122.


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
