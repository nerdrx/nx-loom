# NX Loom

**The mesh is not the document. The layout is.**

A Blender addon for *authored* quad topology. You draw the network of curves that defines the edge flow you want; Loom cuts it into patches, solves one global integer problem so every patch closes, and generates the quads from that. Move a curve, drag the density slider, it rebuilds. Nothing is destructive until you press Apply.

Its sibling [QuadForge](https://github.com/nerdrx/quadforge) is the automatic half of the same problem. QuadForge answers "give me clean quads". Loom answers "give me clean quads **here**, like **this**".

## Why

Auto-retopology has a ceiling, and it's not the algorithm. It's that nobody wrote down what the topology was supposed to be: every vertex is hand-placed truth with zero record of intent behind it, so a remesher has to guess at intent that was never expressed. And an auto layout that lands 90% right usually costs more to fix than a manual one costs to draw.

Loom stores the intent instead. Corners, edge flow, creases, holes — that's the document. The polygons are a build product you regenerate from it.

Manual is the product here; automation only gets to *suggest*. It can propose arcs into a layout you then edit (accepted suggestions become ordinary arcs). Nothing runs on its own, and nothing gets trusted with a complex case.

## How it works

You author three things:

- **nodes** — corners, pinned to the reference surface as `(triangle, u, v)` so the layout survives edits to the sculpt
- **arcs** — curves between nodes, typed *crease* / *flow* / *boundary* / *seam*
- **patches** — the regions those arcs enclose, found automatically

Quad patches get a discrete Coons grid; the 3-/5-/6-sided patches (where the poles go) get split templates. Interiors relax and reproject onto the reference.

### One slider, and everything still closes

A quad patch only closes if opposite sides have equal subdivision counts. Across a whole model that's a global integer problem — every arc's count is a variable, every patch contributes constraints. Loom solves it. That's why:

- one density slider re-grids the entire model and every patch still closes
- a loop you add at the wrist propagates the right ring count up the arm
- you never count loops again

All the constraints come out of one small system rather than a branch per patch arity. An n-sided patch fills by splitting each side once and running spokes to a centre vertex; the split points must satisfy `a[i] + a[i+2 mod n] = c[i]`. The quad rule you already know is what that system reduces to at n=4 — it rediscovers the rule instead of hardcoding it, which is why 5- and 6-sided patches need no new machinery.

Parity is solved over GF(2) in a single linear pass, not hill-climbed. On a closed triangulated surface every arc belongs to two patches, so flipping one fixes one neighbour and breaks the other, and a local search stalls. That actually happened in development: greedy repair left 30 of 80 patches uncloseable; the linear solve leaves 0.

### Loop counts propagate

"Six loops around this wrist" is an ordinary request. Alt+Shift-click the arc and type **6**, or Ctrl+Wheel over it; the solve keeps every patch closed around it, so the change ripples out through the model. Pinned arcs get their own colour with the count printed in the viewport — the quantiser stops being something you trust and becomes something you can see.

### A face budget, not just an edge length

Game work is specified in faces, not millimetres. `Size By: Face Count` solves the edge length for a target count — cheap, because the quantiser can predict the face count without filling any geometry. Subdivision counts are whole numbers, so an exact hit isn't always possible; you get the closest reachable count and the panel tells you how far off budget you landed instead of pretending.

### Draw it once, reuse it everywhere

A retopologised *mesh* is vertices — positions on one specific surface, meaningless anywhere else. A *layout* is intent, and intent transfers. **Retarget** moves a layout from one model onto another, topology and all, using whatever the two models already agree on: matching bone names (rig-to-rig, zero setup), matching empties, or bounding boxes as a rough start.

Holes, seams, arc types and pinned loop counts all survive the move, because only the positions change. And ninety percent right is genuinely useful here: what lands is an editable layout, not a mesh to repair.

### UVs with nothing to infer

An unwrapper has to guess a parameterisation out of a triangle soup and relax it. There's nothing to guess here: a quad patch **is** a grid. Adjacent patches merge into one island by propagating the lattice transform across the shared arc (which only works because the quantiser guarantees matching counts there), and arcs you typed **Seam** stop the merge. A torus, which closes on itself both ways, gets cut where the walk meets itself.

Measured: 1.000x texel density on a uniform layout, 1.5x on a drawn sphere, everything inside 0..1, no degenerate faces.

### A whole LOD set from one layout

Re-solving at a smaller budget changes the counts and *not* the patch structure, so every level is the same surface at a different resolution. UVs, seams, materials, weights and shape keys match across levels because they come from the same source onto the same structure — which is the thing that makes LODs normally painful.

Layouts have a structural face floor (N patches cost faces), so LOD emission stops there and tells you, instead of handing you three identical levels.

### Symmetry lives in the document

Set an axis, draw one half. The mirror is part of the *layout*, so both halves share the nodes on the plane and the seam is welded by construction — no mirror-weld pass, no doubles to merge.

Work on either half: every edit to the mirrored side acts on its authored source, so nothing zombies back or reverts. Near the centre line, clicks snap exactly onto the seam (hold Ctrl, or turn Snap to Seam off, to place things deliberately close). Counts are solved on one half and mirrored, and positions are forced into exact mirror pairs — **bit-exact 0.0 mirror error at every density**, not "approximately symmetric". That matters: reprojecting onto a sculpt whose own triangulation is asymmetric pulls the two halves apart by about a triangle's width. You won't notice it on a sphere. You will on a face.

### Hand edits survive the rebuild

The escape hatch that decides whether any of this survives real use. Move vertices in Edit Mode, hit **Capture Edits**, and the offsets are stored against each vertex's *provenance* — which arc it sits on, where inside which patch — not its index. Re-apply at the same density and it's lossless; change the density and the edits resample onto the new grid instead of vanishing.

## Status

**[Latest release](https://github.com/nerdrx/nx-loom/releases/latest).**

Working: the layout graph, patch discovery, the quantiser, patch fill, the rebuild pipeline, the **Loom Draw** toolbar tool, the viewport overlay and delta layer, and data transfer on Apply. Verified closed and all-quad (Euler check green, zero non-manifold, zero boundary, zero surface deviation) at every density on icosphere, UV sphere and cylinder layouts — including a layout *drawn from nothing* on a sphere. 496 headless checks and 19 GUI checks, green on Blender 5.2.0. A sweep of 117 layouts across spheres, icospheres, cylinders, cones and tori at three densities each resolves every patch.

The suggestion lanes are in: **Suggest Arcs** proposes edge flow from the sculpt's curvature; **From Creases** and **From Primitives** propose hard-surface structure as ghosts you accept or discard; **Support Loops** keeps creases sharp; **Flatten Patch** projects a patch interior onto its fitted boundary plane. Rigging-specific workflows remain future work.

A patch Loom can't close without going non-manifold is dropped and named in the report — it never gets fudged into the mesh.

## Use

1. Install `nx-loom-<version>.zip` (Preferences → Add-ons → Install), or via [NX Hub](https://github.com/nerdrx/nx-hub).
2. Select your reference mesh. **Sidebar → NX Loom → New Layout.**
3. Pick **Loom Draw** in the toolbar and draw on the surface:

| Input | Action |
|---|---|
| Click | chain a straight segment along the surface |
| Drag | freehand arc |
| Ctrl-Alt-drag | **ring cut** — swipe across a limb to loop it in one stroke; the next swipe bridges to it, so a tube is two swipes |
| Ctrl-Alt-Shift-drag | **halo** — drag outward from a point to ring it; two concentric halos make an eye socket's loop band |
| C | **loop cut** — insert a loop through the whole quad strip, like Ctrl+R |
| R | repeat the last ring at the same spacing down the limb |
| 1–4 | switch arc type while drawing |
| Ctrl-click | erase an arc / dissolve a node |
| Shift-drag | move a node — drop it onto another node to merge them |
| Alt-click | retype an arc (flow / crease / boundary / seam) |
| Ctrl-Shift-click | toggle a patch between filled and a hole |
| Ctrl-Wheel | pin how many loops cross the arc under the cursor |
| Ctrl-Alt-Wheel | more or less resolution inside one patch |
| Alt-Shift-click | select an arc, then type its loop count in the sidebar |
| Esc, RMB | end the chain; again to leave the tool |

Whatever is under the cursor lights up before you click, so you can see what Ctrl or Shift is about to grab. With symmetry on, anything placed near the centre line snaps *exactly* onto it — a cyan "mid" marker shows when the snap is armed.

Strokes snap to existing nodes; ending one on an existing arc splits it into a T-junction, and drawing **through** an arc splits both at a shared crossing. Enclose an area and it becomes a patch, filled at once — including a plain closed ring round a limb, which needs no corners. Mark eye sockets and mouth openings as **holes** and they stay empty.

Drawing a loop on a closed mesh bounds two regions: the bit you drew round, and everything else. The leftover is detected and left alone rather than covered in geometry (there's a checkbox if you did want it filled).

4. Set the size: an **edge length**, or a **face budget** if you're working to one. Any patch the solver refuses is drawn **red** in the viewport, counted in the panel, and *Show Problem Patch* takes you to it.
5. Need a vertex somewhere the solver wouldn't put it? Move it in Edit Mode and hit **Capture Edits**. The edit is stored against the layout, not the vertex index — change the density afterwards and it's still there.
6. **Apply** when you want a plain mesh. Your reference's UVs, materials, vertex groups, shape keys and creases come across onto the new topology — so you can point this at a rigged, shape-keyed avatar and keep working.

To trace an existing mesh instead of drawing: select edges in Edit Mode and use **Layout from Selected Edges**.

The panel reports how many patches of each arity it found, so an unexpected 20-sided patch is visible before it becomes a hole.

## Development

```
tests/run_all.sh
```

Runs headless in Blender. `NXL_ONLY=test_03` filters, `NXL_BLENDER=` overrides the binary. `scripts/gui_check.sh` covers the viewport half — tool activation, the modal path, and overlay pixels via an offscreen render — in a real Blender window under xvfb.

Honest note: most of this was written as a pair with an AI. It does the fast 80% — boilerplate, test scaffolding, grepping at 3am. The design is mine: the global solver, the parity solve, the retarget approach. Those decisions live in the spec, and the spec wins arguments.

- `nx_loom/core/quantize.py` and `nx_loom/core/fill.py` are pure numpy and importable without `bpy`. That rule is load bearing — it's what lets the solver be debugged with plain `python3`.
- `SPEC.md` is the binding contract. Read it before changing anything.

## Licence

GPL-3.0, like every Blender addon.
