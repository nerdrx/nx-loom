# NX Loom

**The mesh is not the document. The layout is.**

A Blender addon for *authored* topology. You draw the network of curves that
defines the edge flow you want; NX Loom partitions it into patches, solves one
global integer problem so every patch closes, and generates the quad mesh.
Move a curve or the density slider and it rebuilds. Nothing is destructive
until you press Apply.

Sibling to [QuadForge](https://github.com/nerdrx/quadforge), which is the
automatic half of the same problem. QuadForge answers *"give me clean quads"*.
NX Loom answers *"give me clean quads **here**, like **this**"*.

## Why

Auto-retopology has a ceiling, and the ceiling is not the algorithm — it is
that nobody wrote down what the topology was supposed to be. Every vertex in a
mesh is hand-placed truth with no record of intent, so a remesher has to guess
at intent that was never expressed, and an auto-layout that lands 90% right
costs more to repair than a manual one costs to draw.

NX Loom stores the intent instead. Corners, edge flow, creases and boundaries
are the document; the polygons are a build product regenerated from it.

**Manual is the product.** Automation exists only to *suggest*: it can propose
arcs into a layout you then edit, and accepted suggestions become ordinary
arcs. No automatic pass runs on its own, and none is trusted with a complex
case.

## The idea

You author three things:

- **nodes** — corners, pinned to the reference surface as `(triangle, u, v)`
  so the layout survives edits to the sculpt
- **arcs** — curves between nodes, typed *crease* / *flow* / *boundary* / *seam*
- **patches** — the regions those arcs enclose, found automatically

and NX Loom generates the mesh. Quad patches get a discrete Coons grid;
3-, 5- and 6-sided patches (where the poles live) get split templates. Interiors
relax and reproject onto the reference.

### Draw a topology once, reuse it forever

**Retarget** moves a layout from one mesh onto another, topology and all. A
retopologised *mesh* is vertices — positions on one specific surface,
meaningless anywhere else. A *layout* is intent, and intent transfers.

Landmarks come from whatever the two models already agree on. If both are
rigged, **matching bone names** give a dense anatomical correspondence for
free — no setup at all. Otherwise matching empties, or bounding boxes as a
rough start.

Holes, seams, arc types and locked loop counts all survive the move, because
only the positions change. And being ninety percent right is genuinely useful
here: what lands is an editable layout, not a mesh to repair.

### UVs with nothing to infer

An unwrapper has to guess a parameterisation from a triangle soup and relax it.
There is nothing to guess here: a quad patch **is** a grid. Adjacent patches
merge into one island by propagating a lattice transform across shared arcs —
which works only because the quantiser guarantees matching counts there — and
arcs you typed **Seam** stop the merge.

Measured: **1.000x texel density on a uniform layout**, 1.5x on a drawn sphere,
everything inside 0..1, no degenerate faces. A torus, which closes on itself
both ways, gets cut where the walk meets itself.

### A whole LOD set from one layout

Re-solving at a smaller budget changes the counts and *not the patch
structure*, so every level is the same surface at a different resolution. UVs,
seams, materials, weights and shape keys match across levels because they come
from the same source onto the same structure — which is exactly why LODs are
normally painful.

Layouts have a structural face floor (N patches cost faces), and LOD emission
stops there and tells you, instead of handing you three identical levels.

### A face budget, not just an edge length

Game work is specified in faces, not millimetres. `Size By: Face Count` solves
the edge length for a target count — cheaply, because the quantiser can predict
the face count without filling any geometry. Subdivisions are whole numbers so
an exact hit is often impossible; the closest reachable count is used and the
panel tells you how far off budget it landed rather than pretending.

### Symmetry, on the layout rather than the mesh

Set an axis and draw one half. The mirror is part of the *document*, so both
halves share the nodes sitting on the plane and the seam is welded by
construction — no mirror-weld pass, no doubles to merge.

Counts are solved over one half and copied, and generated positions are forced
into exact mirror pairs, so the result is **bit-exact (0.0 mirror error) at
every density** rather than approximately symmetric. That distinction matters:
reprojecting onto a sculpt whose own triangulation is asymmetric pulls the two
halves apart by about a triangle's width, which you will not notice on a sphere
and will notice immediately on a face.

### Hand edits survive the rebuild

The escape hatch is the part that decides whether any of this survives real
use. Move vertices in Edit Mode, hit **Capture Edits**, and the offsets are
stored against each vertex's *provenance* — which arc it sits on, where inside
which patch — rather than its index. Re-applying at the same density is
lossless; change the density and the edits resample onto the new grid instead
of vanishing.

### One slider re-grids everything, and it always closes

For a quad patch to close, opposite sides need equal subdivision counts. Across
a whole model that is a global integer problem — every arc's count is a
variable and every patch contributes constraints. NX Loom solves it, so:

- one density slider re-grids the entire model and every patch still closes
- a loop you add at the wrist propagates the right ring count up the arm
- you never count loops again

All the constraints come out of one small system rather than a branch per patch
arity. Fill an n-sided patch by splitting each side once and running spokes to
a centre vertex; the split points must satisfy `a[i] + a[i+2 mod n] = c[i]`.
The familiar quad rule is what that system reduces to at n=4 — rediscovered,
not hard-coded — which is why 5- and 6-sided patches need no new machinery.

Parity is solved over GF(2) in a single linear pass rather than hill-climbed.
On a closed triangulated surface every arc is shared by two patches, so
flipping one arc fixes one and breaks its neighbour and a local search stalls:
measured, 30 of 80 patches unsolvable by greedy repair, 0 after the linear
solve.

## Status

**v0.9.0 — draw a topology once, then reuse it on every model you own.**

Working: the layout graph, patch discovery, the quantizer, patch fill, the
rebuild pipeline, the **Loom Draw** toolbar tool, the viewport overlay and the
delta layer, and data transfer on Apply.
Verified closed and all-quad (χ=2, zero non-manifold, zero boundary, zero
surface deviation) at every density on icosphere, UV sphere and cylinder
layouts — including a layout *drawn from nothing* on a sphere. 206 headless
checks plus 8 GUI checks, green on Blender 5.2.0. A sweep of 122 layouts
across spheres, icospheres, cylinders, cones and tori at three densities each
resolves every patch.

Not written yet: the suggestion lanes.

A patch NX Loom cannot quantize, split or fill without going non-manifold is
dropped and named in the report — never fudged into the mesh.

## Use

1. Install `nx-loom-<version>.zip` (Preferences → Add-ons → Install), or via
   [NX Hub](https://github.com/nerdrx/nx-hub).
2. Select your reference mesh. **Sidebar → NX Loom → New Layout.**
3. Pick **Loom Draw** in the toolbar and draw on the surface:

   | Input | Action |
   |---|---|
   | Click | chain a straight segment along the surface |
   | Drag | freehand arc |
   | Ctrl-click | erase an arc / dissolve a node |
   | Shift-drag | move a node |
   | Alt-click | retype an arc (flow / crease / boundary / seam) |
   | Ctrl-Shift-click | toggle a patch between filled and a hole |
   | Esc, RMB | end the chain; again to leave the tool |

   Strokes snap to existing nodes, and ending one on an existing arc splits it
   into a T-junction. Enclose an area and it becomes a patch, filled at once —
   including a plain closed ring round a limb, which needs no corners. Mark
   eye sockets and mouth openings as **holes** and they stay empty.

   Drawing a loop on a closed mesh bounds two regions: the bit you drew round,
   and everything else. The leftover is detected and left alone rather than
   covered in geometry (there's a checkbox if you did want it filled).
4. Set the size: an **edge length**, or a **face budget** if you are working to
   one. Any patch the solver refuses is drawn **red** in the viewport, counted
   in the panel, and *Show Problem Patch* takes you to it.
5. Need a vertex somewhere the solver would not put it? Move it in Edit Mode
   and hit **Capture Edits**. The edit is stored against the layout, not the
   vertex index — change the density afterwards and it is still there.
6. **Apply** when you want a plain mesh. Your reference's UVs, materials,
   vertex groups, shape keys and creases come across onto the new topology —
   so you can point this at a rigged, shape-keyed avatar and keep working.

To trace an existing mesh instead of drawing: select edges in Edit Mode and use
**Layout from Selected Edges**.

The panel reports how many patches of each arity it found, so an unexpected
20-sided patch is visible before it becomes a hole.

## Development

```bash
tests/run_all.sh
```

Runs headless in Blender. `NXL_ONLY=test_03` filters, `NXL_BLENDER=` overrides
the binary. `scripts/gui_check.sh` covers the viewport half — tool activation,
the modal path, and overlay pixels via an offscreen render — in a real Blender
window under xvfb.

`nx_loom/core/quantize.py` and `nx_loom/core/fill.py` are pure numpy and stay
importable without `bpy` — that rule is load bearing, it is what lets the
solver be debugged with plain `python3`.

`SPEC.md` is the binding contract. Read it before changing anything.

## Licence

GPL-3.0, like every Blender addon.
