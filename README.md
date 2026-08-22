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

**v0.2.0 — you can draw a layout and get a mesh.**

Working: the layout graph, patch discovery, the quantizer, patch fill, the
rebuild pipeline, the **Loom Draw** toolbar tool and the viewport overlay.
Verified closed and all-quad (χ=2, zero non-manifold, zero boundary, zero
surface deviation) at every density on icosphere, UV sphere and cylinder
layouts — including a layout *drawn from nothing* on a sphere. 79 headless
checks plus 8 GUI checks, green on Blender 5.2.0.

Not written yet: the delta layer for hand edits, data transfer on Apply, and
every suggestion lane.

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
   | Esc, RMB | end the chain; again to leave the tool |

   Strokes snap to existing nodes, and ending one on an existing arc splits it
   into a T-junction. Enclose an area and it becomes a patch, filled at once.
4. Adjust **Density**. Any patch the solver refuses is drawn **red** in the
   viewport and counted in the panel, so a problem is visible while you draw.
5. **Apply** when you want a plain mesh.

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
