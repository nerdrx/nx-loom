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
  fill:    "coons" | "field" | "hold"  # "hold" = user froze this patch's geometry
}
```

**Pins.** A pin is `(triangle index on the reference's evaluated mesh,
barycentric u, v)`. Pins are how the layout survives edits to the sculpt: the
arc follows the surface, not a stale world position. `co` is recomputed from
the pin whenever the reference changes. A node or path point with `pin: null`
is free in space — that is the from-scratch modeling path.

**Sides are chains.** A patch side may span several arcs. Everything in the
solver works on *side sums*, never on single arcs.

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

1. **Targets.** `tᵢ = arc_length(i) / target_edge`, floored at 1.
2. **Real solve.** Equality-constrained least squares (dense KKT) over the side
   constraints, ignoring integrality and parity.
3. **Round** to nearest integer, clamp to `≥ 1`, honour `n_lock`.
4. **Repair.** Greedy coordinate descent: while any constraint is violated,
   move the arc with the lowest *rounding regret* (`|n − t|` increase) by ±1.
   Bounded iterations; failure is reported per-patch, never silently ignored.

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

## 5. Delta layer (v0.4, specified now so nothing blocks it)

Manual edits to the generated mesh are stored per-patch in patch-local `(u, v)`
coordinates plus a normal offset, in `nx_loom_delta`. On rebuild at the **same**
subdivision counts, deltas are reapplied exactly. On rebuild at *different*
counts they are resampled bilinearly and the report says so. A patch whose
deltas cannot be carried is marked `fill: "hold"` and left untouched rather
than silently flattened.

## 6. Apply

`nxloom.apply` drops `nx_loom_graph` and `nx_loom_delta` and leaves an ordinary
mesh. Data transfer from the reference (UVs, materials, vertex groups, shape
keys, creases) reuses QuadForge's `core/transfer.py`, **vendored** under
`nx_loom/core/vendor/` with the upstream commit recorded in
`nx_loom/core/vendor/PROVENANCE.md`. Vendored files are not edited in place;
fixes go upstream to QuadForge first.

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
- **Rigging (v1.0).** Revolute axes come free from the cylinder fits; rigid
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
| Shift-drag | move the node under the cursor along the surface |
| Alt-click | give the arc under the cursor the current arc type |
| Esc / RMB | end the chain; again to leave the tool |

Straight segments are traced by **interpolating rays, not world positions** —
every sample is re-cast at the surface, so a segment drawn across a bulge wraps
over it instead of tunnelling through. Snapping is defined in *pixels* and
converted to world units per click, so it feels identical zoomed into an ear or
looking at a whole body. Ending a stroke on an existing arc splits it, which is
what makes a T-junction something you draw rather than plan for.

The overlay draws the layout — arcs coloured by type, nodes sized by role, and
**any patch the solver refused, in red**. A layout problem has to be visible
while you are drawing, not discovered later as a hole. A draw handler must
never raise: no region, no graph and a failed batch build are all
early-returns, because an exception inside a draw callback breaks the whole
viewport, not just this overlay.

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
