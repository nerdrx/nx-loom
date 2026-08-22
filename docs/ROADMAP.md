# Where this can go

> Items 1, 2 and 3 shipped in 0.8.0 and 0.9.0. Kept here for the reasoning.

Ideas ranked by what the architecture makes *uniquely* possible, not by how
nice they would be. The layout being the document — a graph of barycentric pins
plus a topology, with a global integer solve over it — is the thing that buys
leverage. Anything that could be built just as well as a normal Blender addon
is not on this list.

---

## 1. Layout retargeting — author your face topology once, reuse it forever

**The pitch.** A layout is not geometry. It is a graph whose nodes are
barycentric pins and whose arcs are surface paths. Given a correspondence
between two meshes, that graph can be *transferred*: draw the topology for a
head once, then drop it onto every other avatar you own.

**Why it is possible here and nowhere else.** Every other retopo tool's output
is vertices. Vertices do not transfer — they are positions on one specific
mesh. A pinned graph is a description of *intent*, and intent transfers.

**How.** The artist places a handful of matched landmarks (eye corners, mouth
corners, chin, ear root) on source and target. Fit a smooth deformation from
those — thin-plate spline or biharmonic — map every node through it, then
re-project onto the target surface and let arcs re-trace as geodesics between
the moved nodes. The topology is unchanged by construction; only the pins move.
Then the artist fixes the three places it got wrong, which is a layout edit,
not a retopo job.

**Risk.** Landmark correspondence is the hard part, and a bad fit puts nodes on
the wrong side of a crease. Mitigation is that the result is an *editable
layout* — being 90% right is useful here, whereas a 90%-right auto-remesh is
worse than useless.

**Size.** Medium. The deformation fit is textbook; the work is the landmark UI
and re-tracing arcs robustly.

---

## 2. A whole LOD set from one document

**The pitch.** Emit LOD0/1/2/3 from a single layout, with *structurally
identical* topology — same patches, same poles, same seams, proportionally
fewer loops.

**Why it is possible here.** Density is one global integer solve. Re-solving at
a smaller budget does not change the patch structure at all, only the counts.
That is a guarantee no decimator can make: every LOD has the same UV layout,
the same seams, the same material boundaries, and deforms the same way.

**Why it matters.** This is precisely what VRChat performance ranks want, and
the reason LODs are usually painful is that a decimated mesh needs its UVs and
weights redone. Here they transfer from the same source, onto the same
structure.

**Size.** Small — the solver already does the work. It is a loop over budgets
plus naming and collection management.

---

## 3. UVs straight out of the layout

**The pitch.** Stop unwrapping. Every quad patch already *is* a `(u, v)` grid,
and arcs already carry a `seam` type. Cut along seam arcs, lay each patch's
grid out flat, pack the islands.

**Why it is possible here.** An unwrapper has to infer a parameterisation from
a triangle soup and minimise distortion by relaxation. We do not infer it — the
patch grid is the parameterisation, exactly, by construction. A patch that is a
`p x q` grid unwraps to a `p x q` rectangle with zero distortion in the
parameter domain, and the only real work is choosing island scale from the
patch's true surface area so texel density stays even.

**Why it matters.** UV control is the other half of the retopo job, and it is
currently a separate manual fight. Doing it in the same document means the seam
you drew *is* the seam you get, and re-gridding at a new density keeps it.

**Risk.** Packing quality, and n-sided patches which have no single rectangle.
Both are tractable; n-gon patches can unwrap per sub-quad.

**Size.** Medium.

---

## 4. Make the integer solve visible

**The pitch.** Hover an arc, scroll, and watch the loop count change — and
watch the change *propagate* across the model as the solver re-satisfies every
patch. Locked arcs shown with a pin.

**Why.** The global quantiser is the cleverest thing in this addon and it is
currently invisible: the artist sees a density slider and has to trust it. Made
interactive, it becomes the thing that teaches the tool. "I want exactly six
loops around this wrist" is a normal request, and right now the only answer is
`n_lock`, which has no UI at all.

**Size.** Small-to-medium. `n_lock` exists and the solver honours it; this is a
modal tool and an overlay.

---

## 5. Per-patch density

**The pitch.** A multiplier per patch — more resolution in the face, less in
the boots — that the global solve respects rather than fights.

**Why here.** QuadForge does adaptive density by curvature, automatically. This
is the authored version: the artist says where, which is the whole thesis of
the addon.

**Size.** Small. It is a per-patch weight on the target lengths of its arcs,
and the quantiser already handles inconsistent targets by least squares.

---

## 6. Rigging from the layout (already SPEC §7)

Cylinder fits give revolute axes for free, patch groups give rigid components,
fillet bands give the only blend zones. Unchanged from the spec; listed here so
the ordering is visible.

---

## Deliberately not doing

- **Auto-layout as the default path.** SPEC §7 is frozen on this. Suggestions
  only.
- **A general n-gon fill.** Refusing a patch and saying why is better than
  quietly emitting something the artist has to find later.
- **Our own data transfer.** QuadForge's is vendored and fixes go upstream.
