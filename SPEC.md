# NX Loom — Specification

**The mesh is not the document. The layout is.**

NX Loom is a Blender addon for *authored* topology. You draw a network of curves
on (or near) a reference surface; NX Loom partitions that network into patches,
solves one global integer problem so every patch closes, and **generates** the
quad mesh. Edit a curve or move one slider and the mesh is rebuilt. Nothing is
destructive until you press Apply.

Sibling to [QuadForge](https://github.com/nerdrx/quadforge), which is the
*automatic* half of the same problem. QuadForge answers "give me clean quads";
NX Loom answers "give me clean quads **here**, like **this**".

**Manual is the product. [frozen]** The artist authors the layout; the addon
guarantees it closes. Automation exists only to *suggest* — it may propose arcs
into a layout the artist then edits, and it is never the path that runs by
default, never applied without the artist accepting it, and never trusted with
a complex case. Every automatic feature in §7 is a proposal generator whose
output is an ordinary editable layout, indistinguishable from a hand-drawn one.
A change that makes any automatic pass run unprompted, or that makes an
automatic result harder to edit than a manual one, is a change against this
spec. The reason is not taste: an auto-layout that is 90% right costs more to
repair than a manual one costs to draw, which is exactly the ceiling that made
this tool necessary in the first place.

This file is the binding contract. Read it before touching code. Sections marked
**[frozen]** must not change shape without a version bump and a note in
`docs/CHANGELOG.md`.

---

## 1. Data model **[frozen]**

The source of truth is a **layout graph**, stored on the generated object as a
JSON string in the ID property `nx_loom_graph`. The generated mesh carries no
authority — it may be deleted and rebuilt at any time.

```
Graph {
  version:   int                       # schema version, currently 1
  reference: str                       # object name of the reference surface, or ""
  nodes:     [Node]
  arcs:      [Arc]
  patches:   [Patch]                   # derived; cached, recomputed on demand
  settings:  { target_edge: float, ... }
}

Node {
  id:     int
  pin:    [tri_index, u, v] | null     # barycentric pin on the reference surface
  co:     [x, y, z]                    # world position; authoritative iff pin is null
  kind:   "corner" | "pole"
}

Arc {
  id:      int
  a:       node_id                     # start
  b:       node_id                     # end
  path:    [[x,y,z], ...]              # dense polyline, endpoints included
  pins:    [[tri,u,v] | null, ...]     # parallel to path; null entries are free
  type:    "crease" | "flow" | "boundary" | "seam"
  rail:    "surface" | "straight" | "arc" | "free"
  n:       int | null                  # solved subdivision count; null until solved
  n_lock:  int | null                  # user-pinned count; the solver treats it as fixed
}

Patch {
  id:      int
  sides:   [[arc_id, reversed], ...]   # ordered CCW loop of directed arcs
  corners: [node_id, ...]              # one per side, side i runs corners[i] -> corners[i+1]
  fill:    "coons" | "field" | "hole" | "hold"
                                       # "hole" = deliberately not filled
                                       # "hold" = user froze this patch's geometry
}
```

**Pins.** A pin is `(triangle index on the reference's evaluated mesh,
barycentric u, v)`. Pins are how the layout survives edits to the sculpt: the
arc follows the surface, not a stale world position. `co` is recomputed from
the pin whenever the reference changes. A node or path point with `pin: null`
is free in space — that is the from-scratch modeling path.

**Sides are chains.** A patch side may span several arcs. Everything in the
solver works on *side sums*, never on single arcs.

**Patch ids are not identity, and raw arc ids are not either.** Patches are
re-derived on every edit, and under symmetry the mirrored half's arcs are
regenerated with fresh ids on every sync. Anything stored against a patch — a
hole, a density override — is keyed on its **canonical** arc set
(`LayoutGraph.canonical_key`: every arc mapped to its mirror source or twin
first), held in settings and re-applied after discovery. A patch and its
mirror share one canonical key, so an attribute set on either side applies to
both — which is also what keeps the output symmetric. With symmetry off the
canonical key equals the raw key. Raw keys are still accepted on read for old
files.

**A cornerless loop is still a region. [frozen]** A ring drawn round a limb has
no junctions and no sharp turns, so it has no natural corners at all. Refusing
it means the most obvious first stroke anyone draws produces nothing, so a
cycle with fewer than three corners is cut into four sides at evenly spaced
nodes and filled as a quad patch.

**Not every discovered region is wanted. [frozen]** A closed loop on a closed
surface produces *two* valid regions: the part you drew round, and the entire
rest of the model. Both are real patches, and filling both sprays geometry over
the whole hull. A region that is more than `6x` the smallest and more than half
the total area is treated as *background*: left alone, reported, never filled
unless `fill_background` is set. This is a heuristic and it is allowed to be —
being wrong here costs one checkbox, while filling the hull costs the artist
their work.

---

## 2. The quantizer **[frozen semantics, heuristic implementation]**

Every arc gets an integer subdivision count `n ≥ 1`. Counts must satisfy, for
every patch:

All of them come from one small system. Fill an n-sided patch by splitting
each side at one point and running spokes to a single interior vertex; the
split points `a` must satisfy

```
a[i] + a[i+2 mod n] = c[i]          c = side subdivision counts
```

Every constraint below is a property of that system, derived at runtime by
`core/quantize.patch_constraint_rows`, not hard-coded:

| Patch | Constraint | Where it comes from |
|---|---|---|
| 4-sided | `Σ side₀ = Σ side₂`, `Σ side₁ = Σ side₃` | left-nullspace rows `(1,0,−1,0)`, `(0,1,0,−1)` |
| odd n (3, 5, 7…) | `Σ all sides` is even, and every `aᵢ ≥ 1` | system is invertible; only integrality binds |
| even n ≠ 4 (6, 8…) | alternating sums over even- and odd-indexed sides each vanish | two-dimensional left-nullspace |

The familiar quad rule is therefore a *special case* the solver rediscovers,
not a branch in the code — which is why 5- and 6-sided patches (where the poles
live) need no new machinery.

Non-quad patches place one interior vertex, split each side once, and emit `n`
tensor sub-grids. `solve_splits` returns `None` — a reportable state, not a
crash — when the counts admit no integer split with every `aᵢ ≥ 1`.

Solve order (`core/quantize.py`):

0. **Floors.** Every side of a non-quad patch needs at least 2 segments. Where
   that side is a single arc, the arc's floor is 2, and *every* pass respects
   it — the GF(2) pass moves in ±1 steps and will otherwise drag a floored side
   straight back down.
1. **Targets.** `tᵢ = arc_length(i) / target_edge`, floored at 1, and **raised
   to the arc's floor before the relaxation runs**. Solving from raw targets
   and clamping afterwards produces a globally inconsistent starting point — a
   pole fan forces its spokes to 2 while every arc around them sits at 1 — and
   no amount of local repair walks that back. Regret is still measured against
   the true targets.
2. **Real solve.** Equality-constrained least squares (dense KKT) over the side
   constraints, ignoring integrality and parity.
3. **Round** to nearest integer, clamp to `≥ 1`, honour `n_lock`.
4. **Repair.** Greedy coordinate descent: while any constraint is violated,
   move the arc with the lowest *rounding regret* (`|n − t|` increase) by ±1.
   Bounded iterations; failure is reported per-patch, never silently ignored.

Steps 2–4 are re-run from several roundings of the same real solution
(`±0.25`, `±0.5`). Greedy repair is a hill-climb and stalls in local minima;
restarting costs almost nothing, stays deterministic, and measurably matters —
22 of 122 swept layouts land on a non-zero shift.

The solve is **seeded with the last successful counts** (`quantize(seed=...)`,
fed from `arc.n`). Solvability is topological; node positions only move the
targets, so an edit that leaves the topology alone cannot make the system
infeasible — the previous counts remain a complete valid solution, and the
seed attempt recovers them whenever the fresh multi-start stalls. This is what
makes "I nudged a vertex and now it will not solve" impossible for
topology-preserving edits. Failed solves are never cached.

This is a heuristic, not the min-cost-flow ILP of Campen/Bommes/Kobbelt 2015.
It is exact on the common cases (single-arc sides, mostly-quad layouts) and
degrades to "this patch could not be quantized, add an arc" — which is a
*reportable* state, not a broken mesh. Upgrading step 4 to real min-cost flow
is a known, isolated improvement.

**Contract:** `quantize(graph) -> (counts: dict[arc_id, int], report)` never
raises on a well-formed graph, and never returns counts that violate a
constraint — an unsatisfiable patch is excluded and named in the report.

---

## 3. Fill **[frozen]**

`core/fill.py` turns one patch + its solved boundary into vertices and quads.

- **4-sided:** discrete Coons / transfinite interpolation from the four
  boundary polylines, then *k* Laplacian relaxation passes on interior
  vertices, then reprojection onto the reference BVH (skipped for free patches).
- **n-sided:** half-sum split into `n` quad sub-patches, each filled as above;
  the interior vertex starts at the centroid and relaxes with everything else.
- Boundary vertices are **owned by the arc**, not the patch. `core/build.py`
  instantiates each arc's vertices once and both neighbouring patches index
  into them — patches are welded by construction, never by distance merge.

**Contract:** `fill_patch(...) -> (verts, quads, boundary_index_map)`; emits
only quads; never emits a vertex not reachable from the boundary.

---

## 4. Rebuild pipeline

```
reference mesh ─┐
                ├─► Surface (BVH + evaluated mesh)  core/surface.py
layout graph ───┘
      │
      ├─► patch discovery (planar-graph face traversal)   core/graph.py
      ├─► quantize                                        core/quantize.py
      ├─► arc resampling to solved counts                 core/surface.py
      ├─► per-patch fill                                  core/fill.py
      └─► weld + orient + write bmesh                     core/build.py
```

Rebuild is explicit (`nxloom.rebuild`) or automatic on graph change when
`scene.nx_loom.auto_rebuild` is set. Auto-rebuild is throttled and always
runs on the *whole* graph — no partial-update cache in v0.1.

## 4b. Symmetry **[frozen]**

Mirroring happens on the **layout**, not the mesh. Both halves share the nodes
that sit on the plane, so the seam is welded by construction — there is no
mirror-weld pass, no doubles to merge, and no tolerance to tune afterwards.

Two distinct relationships, and conflating them corrupts the document:

- `Arc.mirror_of` — this arc was **generated**. Every sync throws all of them
  away and regenerates them, which is why deleting an authored arc removes its
  counterpart for free.
- `Arc.twin` — this arc was **authored**, and happens to be the mirror partner
  of another authored arc. It is never deleted. The pairing exists only so both
  sides receive the same subdivision count.

Marking a hand-drawn arc as `mirror_of` would let the next sync delete and
regenerate the artist's own geometry, churning arc ids and breaking every hole
key and delta that references them.

`sync()` drops derived elements, snaps near-plane nodes exactly onto the plane,
splits arcs that cross it, adopts hand-drawn counterparts as twins (positive
side is always the source, so the choice is deterministic), and mirrors
whatever is left.

**Locks are read from every arc, not from representatives.** A mirrored or
twinned arc is represented by its partner in the solve, so collecting locks off
the representatives alone silently discards any pin on the other half — half
the arcs on a symmetric layout. Conflicting pins on the two halves are reported
rather than resolved by whichever is seen first.

**Counts are solved over representatives.** `build` maps every arc to its
source or twin and quantises the reduced system. A mirrored layout has a
mirrored constraint system, so solving one half solves both — and the counts
come out identical rather than merely similar. Symmetrising counts *after* an
independent solve does not work: the two halves drift apart.

**Positions are then forced into exact pairs.** The layout mirrors exactly but
the generated positions do not: the reference mesh's own triangulation is
asymmetric, so reprojecting onto it pulls the halves apart by up to a
triangle's width (measured ~2e-2 on a sphere — invisible in isolation, very
visible on a face). `symmetrize_verts` copies the positive side onto the
negative one, one-to-one, closest pairs first. A plain per-vertex nearest
search is not enough: two vertices claim the same partner and a third is left
unpaired and quietly asymmetric. Result is bit-exact (0.0 mirror error) at
every density.

Hand edits in the delta layer are **not** mirrored — they are applied after
symmetrisation, per vertex, exactly where they were made.

## 5. Delta layer **[implemented]**

Manual edits to the generated mesh are stored in `nx_loom_delta` against each
vertex's **provenance** — which node it is, where along which arc, or where
inside which patch — never against its index, because an index means a
different thing at a different density.

```
{ "version": 1,
  "offsets": { "p:2:0.333333:0.666667": [dt, db, dn], ... },
  "dims":    { "p:2": [p, q], "a:5": [n], ... } }
```

Offsets are held in a local frame `(tangent, bitangent, normal)` derived from
the surface normal alone, so an edit follows the sculpt if the reference
changes instead of hanging in world space. The frame must be a pure function of
the normal — capture and re-apply have to agree, and determinism is the only
thing that guarantees it.

**Re-applying at the same counts is lossless.** That is the load-bearing
guarantee: if it is not exact, the artist's work degrades every time they touch
the density slider, and the whole non-destructive claim is hollow.

At different counts, offsets are resampled out of the capture-time
displacement grid — linearly along an arc, bilinearly inside a quad patch. Two
things this rules out:

- **The grid resolution is recorded, not inferred from the samples.** An arc
  whose only edit sits at `t = 2/3` would reconstruct as `n = 2`, and the
  offset would smear along the entire arc.
- **The kernel must have local support.** Distance weighting over the stored
  points displaces every other vertex in an edited patch, which breaks the
  same-density guarantee outright. A sparse grid that is zero except where the
  artist moved something is exact at grid points by construction and decays to
  zero away from an edit.

Node edits are density-independent and always exact. An n-sided patch interior
has no `(u, v)` parameterisation, so its edits are carried at the same counts
only; the rebuild report says how many were exact, resampled and dropped rather
than hiding it.

Changing the vertex *count* is refused, not guessed at: adding or deleting
geometry is a layout change, and the error says to draw it instead.

## 6. Apply

`nxloom.apply` drops `nx_loom_graph`, `nx_loom_delta` and the patch-health
cache, leaving an ordinary mesh.

Before dropping them it transfers the reference's data onto the new topology —
UVs (island-constrained), materials, vertex groups, shape keys, creases and
bevel weights. That projection is QuadForge's `core/transfer.py`, **vendored
verbatim** at `nx_loom/core/vendor/qf_transfer.py` with the upstream commit in
`PROVENANCE.md`. It is a large module that took a long saga upstream to get
right (UV seam bleed, weight leaks under exact symmetry, crease arc routing);
re-deriving it here would be a mistake.

Vendored files are **not edited in place**. Fixes go upstream to QuadForge
first, then the file is re-copied and the recorded commit updated. The coupling
is one argument: `apply()` takes a settings object exposing `preserve_*`
booleans, and `None` means preserve everything.

Transfer is opt-out (`scene.nx_loom.transfer_data`) and never fatal — a failure
is reported as a warning and Apply still completes.

## 6a. Retargeting **[frozen]**

`core/retarget.py`. A layout moves from one mesh onto another; the topology is
untouched by construction — only positions and pins change, so holes, seams,
arc types and locked counts all survive.

This is the thing a pinned graph can do that a mesh cannot. A retopologised
mesh is vertices: positions on one specific surface, meaningless anywhere else.
A layout describes *intent*, and intent transfers.

A 3D thin-plate spline is fitted to landmark pairs (kernel `U(r) = r`), every
node and arc sample is warped through it, then projected onto the target and
re-pinned. Under four landmarks there is not enough to determine an affine
part, so the fit degrades to a least-squares similarity rather than producing
nonsense.

Landmarks come from whatever the two models already agree on, in order:
**matching bone names** (two humanoid rigs share them — a dense, anatomical
correspondence sitting right there, and far better than anything inferred from
the shapes), then **matching empties** parented to each mesh, then **bounding
box corners**, which know nothing about anatomy and are a starting point to
edit rather than an answer.

Being ninety percent right is *useful* here, which is the whole reason this
works: the output is an ordinary editable layout, not a mesh to repair.

## 6b. Derived outputs

Both fall out of the layout being the document; neither is a new solver.

### LODs

`nxloom.make_lods` re-solves the same layout at successively smaller budgets.
The patch structure does not change — only the subdivision counts — so every
level is the same surface at a different resolution and every level takes its
UVs, materials, weights and shape keys from the same source. That is the part
a decimator cannot promise, and the reason LODs are normally painful.

A layout has a **structural face floor** (`build.floor_faces`): every side of a
non-quad patch needs at least two segments, so N patches cost faces no matter
what. Asking for fewer is not a solver failure — it is a request the layout
cannot represent, and the answer is a coarser *layout*. LOD emission stops at
the floor and says so rather than emitting duplicate levels.

### UVs

`core/uv.py`. An unwrapper infers a parameterisation from a triangle soup and
relaxes it. There is nothing to infer here: a quad patch **is** a `p x q` grid.

Neighbouring patches merge into one island by propagating a rigid lattice
transform across a shared arc — possible only because the quantiser guarantees
both sides of that arc carry the same count. Merging stops at three things: an
arc typed `seam`, a placement that would overlap what is already laid down (a
surface that closes on itself must be cut, and the cut lands where the walk
meets itself), and a side whose counts do not line up.

A seam prevents *merging*; it does not force a *split*. A cut through the
middle of a flat sheet leaves it connected round the ends and nothing needs to
open. Ringing a patch does separate it.

Islands are scaled by their true 3D face area over their cell count, so texel
density is even — measured 1.000x on a uniform layout, 1.5x on a drawn sphere.
Deriving the area from the patch instead is wrong for n-sided patches, whose
sub-blocks each get credited with the whole patch's area.

An n-sided patch is **one disc**, not one island per sub-block. Its blocks are
a quad fan meeting at a centre, and a fan of n quads cannot be laid rigidly on
a lattice for n != 4, so the rigid constraint is dropped *inside* the patch:
rim points go round the centre at angles proportional to their spacing along
the boundary, at radii following the patch's true proportions. A perfect circle
would turn a thin triangle into a hexagon (9.8x texel spread on a cone's slant
patches, versus 2.2x this way).

Non-quad patches still do not merge with their neighbours — each is its own
island. Merging them is general parameterisation, not a lattice walk.

## 7. Suggestion lanes

Suggestions, not automation. Each lane emits *candidate arcs* into the layout,
flagged `suggested`; the artist accepts, edits or deletes them, and accepted
arcs become ordinary arcs with no memory of where they came from. No lane runs
on its own, none is on by default, and none is allowed to touch the mesh —
they write arcs, and arcs are the only thing they write.

- **Organic (v0.2, first lane).** QuadForge's native 4-RoSy field → trace
  separatrices out of singularities → motorcycle graph → simplify → *proposed*
  arcs. Plus symmetry mirroring of arcs the artist already drew, and a
  landmark-snapped template library (eye ring, mouth ring, ear, deltoid) that
  the artist places and drags — placement is a manual act, the template only
  saves the drawing.
- **Hard surface (v0.3).** Planar region growing + RANSAC cylinder/cone/sphere
  fits + fillet strips (near-constant principal curvature between two
  primitives). Intersection ridges are offered as crease arcs and fillets as
  bands with a support-loop count. These are the cases where detection is
  reliable *and* checkable at a glance; anything ambiguous is simply not
  offered rather than guessed at.
- **Rigging (future).** Revolute axes come free from the cylinder fits; rigid
  components are patch groups; fillet bands are the only blend zones. Same
  rule: proposed rig, artist confirms.

A lane that cannot produce a confident suggestion produces none. Partial or
speculative output is worse than silence here, because the artist has to read
and reject it.

## 8. UI surface

`View3D > Sidebar > NX Loom` for state; the toolbar for authoring.

**Loom Draw** is a `WorkSpaceTool`, not a hotkey. Its keymap is scoped to the
tool, so LMB means "draw an arc" only while it is active and no existing
Blender binding is displaced:

| Input | Action |
|---|---|
| Click | chain a straight-on-surface segment from the pending anchor |
| Drag | freehand arc |
| Ctrl-click | erase the arc under the cursor, or dissolve a valence-2 node |
| Ctrl-Alt-drag | ring cut: swipe across a limb to loop it in one stroke |
| Ctrl-Alt-Shift-drag | halo: drag outward from a point to ring it (eyes, mouths) |
| Shift-drag | move the node under the cursor along the surface; drop it on another node to merge them |
| Alt-click | give the arc under the cursor the current arc type |
| Ctrl-Shift-click | toggle a patch between filled and a hole |
| Ctrl-Wheel | pin the loop count across the arc under the cursor |
| Ctrl-Alt-Wheel | more or less resolution inside one patch |
| Alt-Shift-click | select an arc, to type its loop count in the sidebar |
| C | loop cut: preview and insert a loop through the quad strip under the cursor |
| R | repeat the last ring cut at the same spacing |
| 1–4 | arc type: flow / crease / boundary / seam |
| Esc / RMB | end the chain; again to leave the tool |

**Crossing an arc splits both.** A stroke that passes through an existing arc
gets a shared junction there (`authoring.find_crossings` +
`commit_path`): a contiguous run of stroke samples within reach of one arc is
one crossing, runs touching the stroke's ends are the anchors' business, and a
crossing near an existing node reuses it. A line floating over another makes
the layout non-planar and discovery mis-traverses it, so this is correctness,
not convenience. Ring cuts and halos do not crossing-split (they are placed on
open ground; revisit if that assumption dies).

Straight segments are traced by **interpolating rays, not world positions** —
every sample is re-cast at the surface, so a segment drawn across a bulge wraps
over it instead of tunnelling through. A ray crossing a limb hits the near
wall, the far wall and whatever is behind, so the **nearest** hit is taken —
you draw on what you can see — and a deeper crossing is used only when the
nearest one would tear the stroke. Choosing by shortest total path instead is
wrong: a flat wall behind the model beats curving round the limb in front of
it, and the stroke jumps to the far surface.

**Ring cuts** (`core/contour.py`, `ops.draw.commit_ring`): the swipe and the
view direction span a plane; marching-triangles cross-section, chained by
quantised endpoint identity, gives closed loops; the loop nearest the stroke is
the one meant — never the torso behind the limb. The stroke is sampled and
clamped to its surface hits, because a natural swipe overshoots the silhouette.
The ring is emitted as four even arcs between four nodes (the shape discovery
already gives cornerless loops), with the first node anchored under the stroke
start so successive rings correspond.

**Halos** (`ops.draw.commit_halo`): a circle in the tangent plane at the
centre, projected onto the surface — faithful at socket scale; head-scale
loops are ring cut's job. The first node anchors where the drag released, so
the artist places the ring's corners, and halos join the same last-ring chain
as ring cuts, so concentric halos bridge into a loop band.

**Strokes are faired at commit** (`authoring.fair_path`): freehand jitter is
low-passed out of the polyline, endpoints fixed, reprojected as it relaxes.
Straight rails and cross-section rings never smooth — they have no stroke to
be jittery. The deferred-rebuild queue stores the object's NAME; a bpy
reference held across frames dies if the object is deleted first.

**Loop cut** (`core/loopcut.py`): walks the quad strip both ways from the
clicked arc, crossing each patch on the transfinite iso-curve at the clicked
fraction (entry fraction f maps to 1−f on the opposite side — the sides run
antiparallel around the boundary). Stops at non-quads, holes and boundaries;
detects closure by revisiting the start. The planned polyline is committed
through `commit_path`, whose crossing machinery makes the junctions — the walk
plans, the commit builds. Closed loops commit as two halves (no free
endpoints otherwise).

**Ring bridging** (`bridge_rings` in contour + `ops.draw.bridge_rings`): each
new ring auto-connects to the previous one with four straight-rail wall arcs.
Pairing is nearest-neighbour and must be bijective (chain winding is
arbitrary); rings farther apart than their own circumference are refused, which
is what keeps a ring on each leg from bridging the gap between them.

**Sync must never duplicate hand-drawn geometry.** Twin adoption uses a
tolerance proportional to the arc (seam tolerance is for the seam); and when no
arc-to-arc correspondence exists — counterpart rings anchored on opposite sides
decompose into arcs rotated half a ring apart — a would-be mirror whose path
already lies along authored arcs is skipped rather than doubled.

A node with no arcs is a **legitimate state**, not an error. Placing points
before connecting them is how you lay out corners first, and a node with no
arcs simply has no rotation system. Snapping is defined in *pixels* and
converted to world units per click, so it feels identical zoomed into an ear or
looking at a whole body. Ending a stroke on an existing arc splits it, which is
what makes a T-junction something you draw rather than plan for.

**One colour, one meaning.** Warm tones (orange, red, amber) are reserved for
states — warnings, failures, snaps; arc types stay in the cool range. The
viewport legend enumerates the palette; keep it in step with any new colour.
State fills (red wash for failing patches, grey for background, hole
outlines), subdivision ticks, the seam trace and depth-faded x-ray are all
part of the overlay contract and individually toggleable. Depth-tested overlay
passes MUST run under a small toward-camera NDC bias: the arcs lie exactly on
the surfaces in the depth buffer, and an unbiased test is a coin flip that
renders the front of the layout at back-side strength. Any harness check of a
depth-tested pass must first lay real depth into the buffer, or the pass wins
trivially and occlusion bugs are invisible.

Whatever is under the cursor is highlighted in amber before you click, so it is
clear what Ctrl or Shift will grab. It is driven from mouse-move via the tool
keymap, which is only affordable because surfaces are cached; it throttles to
2 pixels and only requests a redraw when the highlighted thing changes.

The overlay draws the layout — arcs coloured by type, nodes sized by role, and
**any patch the solver refused, in red**. A layout problem has to be visible
while you are drawing, not discovered later as a hole. A draw handler must
never raise: no region, no graph and a failed batch build are all
early-returns, because an exception inside a draw callback breaks the whole
viewport, not just this overlay.

The overlay's draw handlers and the hover operator read the layout through
`ops.layout.peek_graph`, a read-only parse cached against the stored blob —
they run per redraw and per mouse move, and parsing the JSON each time cost
18.6 ms on a large layout. Peeked graphs must never be mutated; writers go
through `get_graph` → `set_graph`, which parses fresh so a cancelled modal can
discard its edits. Panel code must not run the solver per draw either
(`floor_faces` is cached the same way).

A failing patch must say why, in the artist's numbers (`core/diagnose.py`),
and every one-click repair is validated on a document copy before it touches
the real graph — a fix that fails or widens the failure is never applied.

A gesture that does nothing must say why. An operator reachable from a click
never returns CANCELLED silently — no reference set, nothing under the cursor,
whatever it is, it goes to `self.report`.

**Rebuild cost must be proportional to the edit, not the layout.** Sync skips
via a content signature over the authored half; mirrors whose source arc is
unchanged are kept, not regenerated; patch fills and the count solve are
memoised on what they read. Cache keys round at 1e-4 world units, because seam
geometry oscillates by pin round-trips (~1e-5..1e-3) between syncs and any
tighter key never matches. When editing sync code, keep `_authored_signature`
in step with what sync actually reads, and never let a per-item cache outlive
the thing it depends on — a stale "covered" verdict would hide a deleted
counterpart, which is why coverage is re-verified (fast, via KD-tree) rather
than cached.

**Mirrored-side patches are constructed, never rediscovered**
(`symmetry.enforce_mirrored_patches`, run after every discovery). Discovery
depends on reference normals and a corner-angle threshold, and the sculpt's
triangulation is not symmetric — a borderline call flips on one side and two
exactly-mirrored regions get different constraints, which is how one cheek of
a fully mirrored layout fails alone. Only fully-derived regions are replaced;
twinned regions keep their own discovery.

**Unpaired arcs are a first-class warning.** `symmetry.unpaired_arcs` lists
authored off-plane arcs with no partner of any kind; the overlay draws them in
warning orange and the panel explains them, because two independently-drawn
halves quantise independently and produce exactly the confusing symptom of one
side failing to solve. `nxloom.symmetrize_side` repairs by keeping one side
(scope: unpaired only, or also replacing twins for exact geometric symmetry).

**Seam snap.** With symmetry on, any landing point (anchor click, stroke end,
node drag) within the pixel snap radius of the mirror plane is clamped exactly
onto it (`authoring.plane_snap`, threaded through `resolve_anchor`), with a
"mid" marker shown beforehand. Exactly-on-plane nodes are what sync shares
between the halves, so near-miss clicks would otherwise mirror into
near-duplicate nodes.

Adjusting a loop count does **not** rebuild the mesh per notch. It used to, so
wheel events queued behind the rebuilds and the count overshot badly on a heavy
layout; the pin now lands immediately and one coalesced rebuild follows the
burst. Anything driven from a wheel or a drag must debounce the same way.

Every `poll()` reads the active object through `ops.layout.active_object`.
`context.active_object` does not exist in restricted contexts and raises rather
than returning None, and a poll that raises spams the console on every redraw.

Layout editing is never Blender's Edit Mode. The generated mesh is a build
product and stays read-only in the UI until Apply.

## 9. Distribution

- Repo `nerdrx/nx-loom`, public, GPL-3.0 (Blender addon, same as QuadForge).
- `package.sh` emits `nx-loom-<version>.zip` containing the `nx_loom/` package.
- NX Hub: `blender-addon` kind, overlay-only. `registry/overrides.json` entry
  in nx-hub with `assetPattern: "nx-loom-*.zip"` and
  `addonsDir: "~/.config/blender/5.2/scripts/addons"`.

## 10. Testing

Headless, one Blender process, same shape as QuadForge's suite:
`tests/run_all.sh`, `NXL_ONLY=`, `NXL_BLENDER=`.

The viewport half is covered by `scripts/gui_check.sh`, which runs a real
Blender window under xvfb. Two traps are baked into that script because both
produced false results:

- **`XDG_RUNTIME_DIR` must be cleared, not just `WAYLAND_DISPLAY`.** Blender's
  Wayland backend falls back to the socket name `wayland-0` and finds it
  through the runtime dir, so clearing only `WAYLAND_DISPLAY` still opens a
  window on the developer's real desktop — and a full-desktop screenshot then
  "passes" a colour check on the wallpaper.
- **`screen.screenshot` is useless under llvmpipe.** It reads the window's
  front buffer and returns solid black whether the overlay drew or not. Verify
  overlay pixels with a `GPUOffScreen` render instead; it starts black and only
  the overlay writes into it.
Pure-math modules (`quantize`, `fill`) must be importable and testable
**without bpy** — they take and return plain numpy/lists. That rule is load
bearing: it is what makes the solver debuggable outside Blender.
