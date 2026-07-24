# CLAUDE.md

Context document for Claude Code, summarizing everything built and decided
in the chat-based development of this project so far. Read this before
making changes -- it captures not just *what* exists but *why*, including
dead ends avoided and decisions that would otherwise look arbitrary.

---

## Who this is for / project background

Thomas is a medical researcher/developer building surgical planning tools
for **microtia reconstruction** (specifically scutum grafting using pinna
cartilage). He's a working developer but newer to Python, comfortable with
3D Slicer, Blender, SimpleITK. Works on Windows. This summer project spans
two closely related sub-projects that are now being merged into one Slicer
extension:

1. **Curvature Project v4** -- an existing, separately-developed Python
   tool (its own `.venv`, Python 3.12.10) that compares a scutum defect
   mesh against a pinna mesh and generates a heatmap of the best cartilage
   graft harvest sites. This already works and its own project folder
   stays as a reference-only, unedited copy -- but its actual algorithm
   has since been *ported* into the extension itself (`core/curvature/`)
   so the extension doesn't need to run it as a separate process; see
   "Curvature Project v4 integration" below for the full story.

2. **Ear Reconstruction Planner** (this project) -- a 3D Slicer extension
   that automates producing the two input meshes Curvature Project v4
   needs (the scutum defect mesh and the pinna mesh) from a CT scan,
   replacing a previously fully-manual Slicer segmentation + Blender
   cropping workflow. This is what's being migrated into Claude Code.

**Priority above all else:** this must be usable by a surgeon with zero
Python/segmentation experience. Every design decision (thresholds exposed
as sliders not raw values, drawing on 3D surfaces instead of typing
coordinates, one-time dependency install, plain-language landmark
instructions) serves that constraint.

---

## Current status (read this first)

As of the end of the last session, Thomas confirmed the **entire scutum
and pinna pipelines work end-to-end in real Slicer**, from landmark
placement through segmentation through drawing/isolating the final
meshes ("everything is looking awesome"). This is a real milestone --
most of this document's "Bugs already found and fixed" and "Dead ends"
entries exist because of the debugging that got here. Concretely tested
and working:
- Setup -> DICOM load -> scutum landmarks (2-point) -> scutum review
  (bone-wall threshold segmentation) -> scutum draw (outline + isolate
  defect patch).
- Pinna landmarks (1-point + side) -> pinna review (skin-surface
  threshold segmentation) -> pinna draw (outline + seed point + canal
  marker + isolate pinna patch, cropped toward the ear canal and cleaned
  of disconnected islands).

**Not yet exercised in this project's testing:**
- The **Verify page** (page 8) and the **Curvature page** (page 9)'s Qt
  widgets specifically -- nothing in this conversation history has walked
  the actual Slicer wizard past isolating the pinna patch, so
  `page_curvature.py`'s button wiring, table population, and the
  scalar-visibility fix for the heatmap's vertex colors are all
  unconfirmed against a real Slicer install (same "flagged, not yet
  runtime-tested" status as several earlier pieces in this doc).
- **The Curvature Project v4 subprocess integration itself, however, IS
  now confirmed working** -- Curvature Project v4 was added into this repo
  (`Curvature Project v4/` at the repo root, own working `.venv`) and
  `curvature_integration.py` was rewritten to auto-locate it (no more
  hardcoded placeholder paths), stream live progress, and avoid a
  stale-output false-success bug. Ran `main.py` directly through the new
  integration module (outside Slicer, via its own venv's `python.exe`)
  against its own test meshes and confirmed a full real run: 300
  candidates scored, top 15 ICP-refined, heatmap + ranked CSV produced,
  in ~19s. See "Curvature Project v4 integration" below for details. What's
  still unconfirmed is only the Slicer-side plumbing around that (loading
  the model, showing its colors, the results table), not the subprocess
  bridge itself.

  **Update:** the subprocess/venv bridge described just above was since
  deliberately replaced with an in-process port (`core/curvature/`) that
  needs no separate Python/venv at all -- see "Curvature Project v4
  integration" below for the current architecture and "Dead ends
  deliberately avoided" for why the subprocess approach, while genuinely
  working, was still worth replacing. The Slicer-side UI plumbing this
  paragraph flags as unconfirmed is *still* unconfirmed -- the port
  changed what runs underneath it, not that layer itself.

**Known still-open issue:** the "Reset All Points" button on the scutum
landmarks page was reported broken early on and was **never actually
debugged** -- see Known Issues #1 below. This is the most likely place to
start if Thomas reports a new problem without more specific context, or
if he mentions "reset" not working.

**Likely resolved but never explicitly reconfirmed:** the "Slicer crashes
opening the module before a DICOM volume is loaded" report (Known Issues
#6) -- two speculative fixes were applied without a confirmed root cause,
and Thomas hasn't mentioned this crash again despite many subsequent
sessions of heavy module use (which all require opening the module first).
Treat as probably fixed, but if it resurfaces, that's a strong signal one
of the two speculative fixes wasn't the actual cause and a real crash
log/traceback is needed.

If picking this project back up cold: skim "Bugs already found and
fixed" and "Dead ends deliberately avoided" below before touching
`roi_crop.py`, `mesh_isolate.py`, or anything that pulls/pushes a volume
or mesh to/from Slicer -- there is a lot of hard-won, non-obvious context
there (especially the RAS/LPS coordinate-convention gotchas, bugs #8 and
#12, which are general Slicer API properties that will bite any new code
touching `sitkUtils` or `slicer.util.loadModel` the same way if not
handled).

---

## High-level architecture

A single Slicer extension, `EarReconstructionPlanner/`, implemented as a
**scripted module** (no compiled C++, no CMake build step needed for
development -- see "Installing for testing" below). It's a 10-page wizard:

```
0. Setup              -- one-time Python dependency install
1. DICOM load         -- confirm which already-loaded scan to use
2. Scutum landmarks   -- place 2 points defining the ear canal axis
3. Scutum review      -- run/adjust bone-wall threshold segmentation
4. Scutum draw        -- surgeon draws the defect outline on the 3D mesh
5. Pinna landmarks    -- place 1 point + pick left/right ear
6. Pinna review       -- run/adjust skin-surface threshold segmentation
7. Pinna draw         -- surgeon draws the pinna outline on the 3D mesh
8. Verify             -- surgeon checkboxes confirming both meshes are correct
9. Curvature          -- runs the curvature comparison in-process, loads heatmap
```

### Why a wizard instead of a normal Slicer module UI

Slicer modules are usually a single panel. This one deliberately uses a
`QStackedWidget` with Next/Back navigation (a well-established pattern in
Slicer, e.g. MONAI Label/nnU-Net plugins) because the surgeon should only
ever see the one relevant step, in plain language, not a dashboard.

### Why `.ui` files + separate Python controllers (not code-only widgets)

Explicit requirement: Thomas wants to be able to open the interface in
**Qt Designer** and edit it visually (rearrange buttons, restyle, resize)
without touching Python logic. Every page's visual layout lives in
`Resources/UI/page_*.ui`; the matching `EarReconstructionPlannerLib/pages/page_*.py`
only wires up logic by widget *name* (e.g. `self.ui.runButton.clicked.connect(...)`).
As long as object names in the `.ui` are kept consistent (documented in
each page's `.py` file's module docstring), the `.ui` files can be freely
redesigned. This is also why the top-level `EarReconstructionPlanner.ui`
exists separately from the per-page `.ui` files -- it just holds the
`QStackedWidget` shell + Back/Next buttons + status label.

---

## Directory structure (as of last working state)

```
EarReconstructionPlanner/
├── EarReconstructionPlanner.py          # Slicer module entry point (ScriptedLoadableModule)
├── README.md                             # install instructions, Qt Designer notes, status
├── EarReconstructionPlannerLib/
│   ├── __init__.py                       # adds itself to sys.path (see "config.py" gotcha below)
│   ├── config.py                         # ALL tunable constants -- see below
│   ├── dependencies.py                   # pip_install wrapper for Setup page
│   ├── wizard_state.py                   # WizardState dataclass + PAGE_ORDER list
│   ├── curvature_integration.py          # in-process bridge to core/curvature/ (no subprocess/venv -- see below)
│   ├── core/                             # segmentation logic, Slicer-independent, unit-testable
│   │   ├── io_utils.py                   # DICOM/volume loading (resample_to_isotropic() kept but unused live -- see below)
│   │   ├── landmarks.py                  # EarCanalLandmarks (2-point axis), .fcsv/.json loaders
│   │   ├── pinna_landmarks.py            # PinnaLandmarks (1-point + side)
│   │   ├── roi_crop.py                   # ROI mask building + cropping (see gotchas below)
│   │   ├── segment_threshold.py          # Stage A: bone wall segmentation (ear canal)
│   │   ├── segment_pinna_threshold.py    # Stage A: skin surface segmentation (pinna)
│   │   ├── segment_dl.py                 # Stage B hook: trained model, falls back to Stage A
│   │   ├── postprocess.py                # speck removal, hole filling, smoothing
│   │   ├── mesh_export.py                # label map -> trimesh via marching cubes
│   │   ├── mesh_isolate.py               # drawn-loop -> isolated mesh patch (shared logic)
│   │   └── curvature/                    # in-process port of Curvature Project v4 (see below)
│   │       ├── mesh_io.py, signature.py, scoring.py, candidates.py, footprint.py  # ported verbatim
│   │       ├── descriptors.py            # shape index/curvedness via trimesh.curvature (replaces pymeshlab)
│   │       ├── geodesics.py              # geodesic distance via scipy dijkstra (replaces potpourri3d)
│   │       ├── registration.py           # ICP via trimesh.registration (replaces open3d)
│   │       ├── heatmap.py                # hand-rolled colormap (replaces matplotlib)
│   │       └── pipeline.py               # orchestration (ported from Curvature Project v4's main.py)
│   └── pages/
│       ├── base_page.py                  # WizardPage interface: on_enter/on_leave_next/on_leave_back
│       ├── page_setup.py
│       ├── page_dicom_load.py
│       ├── page_scutum_landmarks.py
│       ├── page_scutum_review.py
│       ├── page_scutum_draw.py
│       ├── page_pinna_landmarks.py
│       ├── page_pinna_review.py
│       ├── page_pinna_draw.py
│       ├── page_verify.py
│       └── page_curvature.py
└── Resources/UI/
    ├── EarReconstructionPlanner.ui        # top-level shell
    └── page_*.ui                          # one per page, editable in Qt Designer
```

---

## `core/` module reference (the actual segmentation pipeline)

This is the part that was built and tested FIRST, standalone (outside
Slicer, via a throwaway `main.py` CLI script and synthetic test volumes),
before any Slicer UI existed. It has no dependency on `slicer`, `qt`, etc.
-- every function takes/returns plain SimpleITK images, numpy arrays, or
trimesh meshes. This is deliberate and should be preserved: it's what
makes this logic testable without launching Slicer at all.

### `config.py`
Every tunable number lives here with a plain-English comment: voxel
spacing, thresholds (separate air/bone for ear canal, separate skin/air
for pinna), ROI sizes, wall thickness, smoothing amounts, min-cases-to-
train. If tuning behavior, this is the first place to look.

### Ear canal / scutum pipeline
- **`landmarks.py`**: `EarCanalLandmarks` dataclass, 2 points
  (`canal_opening`, `near_eardrum`). Originally 4 points
  (`reference_outer`/`reference_inner` fixed the bounding-plane tilt) --
  **reduced to 2, see the "4-point tilted-plane ROI" dead end below for
  why.** `validate()` does forgiving sanity checks (plausible canal
  length, points not coincident). `from_slicer_fcsv()` / `from_json()`
  were built for CLI testing before the Slicer wizard existed -- may not
  be needed going forward but harmless to keep.
- **`roi_crop.py`**: builds a truncated-cylinder ROI mask from the 2
  landmarks (`build_roi_mask`), loosely adapted from the approach in
  Matin-Mann et al. (2025)'s external-ear-canal-implant segmentation paper
  (mean Dice 0.909 using landmarks + threshold + connected-component
  cleanup, no ML needed) -- their version tilts the bounding planes using
  2 extra landmarks per end; this one keeps the planes perpendicular to
  the canal axis instead (see the dead-ends section for why). **Critical
  gotcha, already fixed once**: `build_roi_mask()`
  evaluates every voxel in whatever image it's given -- calling it
  directly on a full-resolution real CT causes a multi-GB memory error
  (hit this for real during testing: 850x850x700 voxel scan -> tried to
  allocate 2.66 GiB). Fix: `crop_to_landmark_region()` (coarse,
  corner-math-only crop, ~15mm margin default) MUST be called first to
  shrink the volume before `build_roi_mask()`. Same issue and same fix
  pattern (`crop_to_point_region`) applies to the pinna's
  `build_spherical_roi_mask()`. **Any new caller of these mask-building
  functions must coarse-crop first** -- this isn't optional.
- **`segment_threshold.py`**: Stage A bone-wall segmentation. Key design
  point: the clinically useful output is the **bone wall**, not the
  air-filled lumen. But the air lumen is segmented FIRST internally
  (`_segment_air_lumen`, private) as a scaffold, because air-vs-tissue
  contrast is far more reliable on CT than bone-vs-soft-tissue contrast.
  Then `segment_bone_wall()` (the public entry point) dilates that air
  mask outward by `BONE_WALL_THICKNESS_MM` and thresholds for **bone**
  only within that thin shell. The air mask itself is never exported.
  Connected-component selection uses "closest blob to the
  canal_opening->near_eardrum axis line" to reject unrelated air/bone
  pockets that fall inside the ROI.
- **`segment_dl.py`**: Stage B hook. Currently just calls Stage A every
  time (`_load_model`/`_run_model` are `NotImplementedError` stubs).
  Structured so a future trained model (nnU-Net was the original plan;
  **MedSAM2 was considered and explicitly rejected** -- see "Dead ends"
  below) can slot in later without any UI changes, with automatic
  fallback to Stage A if the model directory doesn't exist or fails to
  load/run.

### Pinna pipeline
- **`pinna_landmarks.py`**: `PinnaLandmarks` -- just `ear_center` (1 point)
  + `side` ("left"/"right", picked via radio buttons, never inferred).
  Much simpler than the ear canal's 2 points because Stage A here doesn't
  need an axis, just a search region.
- **`segment_pinna_threshold.py`**: Stage A. **Important**: this does
  NOT attempt to isolate cartilage by intensity -- cartilage-vs-skin
  contrast on CT is poor/unreliable (confirmed via literature research
  earlier in this project; no published CT-based cartilage segmentation
  approach was found, only MRI-based ones). Instead it finds the outer
  **skin** surface (skin-vs-air contrast is excellent) within a spherical
  ROI around `ear_center`. The result is a solid blob of "head skin near
  the ear" -- head and pinna are still joined, since they're physically
  continuous tissue. The drawing step (next) does the actual isolation.

### Shared logic
- **`mesh_export.py`**: label map -> trimesh via marching cubes + light
  Laplacian smoothing. `label_map_to_mesh()`'s returned in-memory mesh is
  in RAS mm (matching every other in-memory value in this pipeline), but
  `export_mesh()` **writes files with vertices flipped to LPS** -- see
  bug #12 below for why this is necessary (STL has no coordinate-system
  field, and `slicer.util.loadModel()` always assumes LPS for such
  files). `flip_ras_lps_points()` (self-inverse, negates X/Y) is the
  point-array equivalent of `io_utils.flip_ras_lps()` for this purpose.
  **Any code loading one of these files back via `trimesh.load()`
  (bypassing Slicer) for coordinate math against Slicer-native RAS points
  must flip it back to RAS first** -- see `page_scutum_draw.py`/
  `page_pinna_draw.py` for the pattern. Meshes do **not** need to be
  watertight (confirmed by Thomas -- the scutum/pinna outputs are open
  surface patches, not solids).
- **`mesh_isolate.py`**: the surgeon-drawn-outline -> isolated-patch logic,
  shared between the pinna and scutum draw stages (and originally
  motivated by the scutum "draw on the ceiling to mark the defect" idea).
  Approach: build a vertex-adjacency graph (networkx, mirroring the same
  dependency/mental model Curvature Project v4 already uses for its own
  geodesic patch extraction), flood-fill from a surgeon-provided seed
  point with the drawn loop's vertices acting as a barrier, include the
  loop vertices in the output for a clean boundary. The seed vertex is
  found by nearest-neighbor search *excluding the loop's own vertices*
  (see bug #9 below for why that matters), not a plain nearest-vertex
  lookup across the whole mesh. Has safety checks: raises a clear error
  if the loop has <3 distinct points (reporting how many were actually
  found, to help distinguish "too few drawn" from "curve didn't snap to
  the surface"), if the loop encloses no non-boundary vertices at all, or
  if the fill reaches >90% of the mesh (usually means the loop has a gap
  and isn't actually closed). Tested successfully against a synthetic
  "head with ear bump" mesh. Also has `crop_toward_canal()`, pinna-only:
  after isolation, removes any part of the patch on the interior side of
  a plane through a surgeon-placed marker (see "Mark Canal Opening" in
  `page_pinna_draw.py`), oriented using the *ear canal's*
  `canal_opening`->`near_eardrum` direction (position and direction are
  deliberately separate arguments -- see bug/feature list below for why),
  extended outward by `PINNA_CANAL_CROP_MARGIN_MM`. Paired with
  `keep_connected_component_containing()`, which discards whatever's left
  disconnected from the surgeon's seed point after that crop -- a single
  plane cut doesn't always sever the head-interior material cleanly in
  one piece.
- **`postprocess.py`**: speck removal (min volume threshold), hole
  filling, light morphological smoothing. Engine-agnostic (works on output
  from either Stage A or a future Stage B).
- **`io_utils.py`**: DICOM/volume loading (folder of .dcm or single
  NRRD/NIfTI/etc. file via SimpleITK). `resample_to_isotropic()` still
  exists but is **no longer called anywhere in the live pipeline** --
  removed deliberately (see "Isotropic resampling removed" below).

### Curvature comparison (`core/curvature/`)
In-process port of the standalone Curvature Project v4 (the final wizard
step's scutum-defect-vs-pinna comparison). File-for-file mirror of that
project's own `src/` layout, so the two stay easy to cross-reference:
`mesh_io.py`, `signature.py`, `scoring.py`, `candidates.py`, `footprint.py`
are ported verbatim (they never touched a compiled-extension dependency).
`descriptors.py`, `geodesics.py`, `registration.py`, and `heatmap.py` are
rewritten to drop pymeshlab/potpourri3d/open3d/matplotlib in favor of
trimesh/scipy equivalents already required elsewhere in this project --
see "Curvature Project v4 integration" below for the full story on why
this exists and each module's own docstring for exactly what it replaced
and why. `pipeline.py` is the orchestration entry point (ported from
Curvature Project v4's `main.py`), called by `curvature_integration.py`.

---

## Wizard/Slicer-specific files

### `wizard_state.py`
`WizardState` is a single shared dataclass instance, created once in
`EarReconstructionPlanner.py`'s `setup()`, passed to every page controller.
Holds: the loaded volume node, both landmark objects, both label map
nodes, both model nodes (needed for surface-constrained curve drawing --
see below), both final mesh paths, verify checkboxes, heatmap output path,
a scratch `working_dir`. **`PAGE_ORDER`** is the single source of truth
for page sequence -- reordering/adding/removing a page is a one-line
change here; `EarReconstructionPlanner.py` builds everything from this
list, nothing is hardcoded elsewhere.

### `base_page.py` / page controllers
Every page controller subclasses `WizardPage` and implements:
- `on_enter()` -- called every time the page becomes visible (including
  navigating back to it); wire up button connections and refresh from
  state here.
- `on_leave_next()` -- returns `(bool, message)`; do the page's actual
  work here (run segmentation, save mesh, etc.) and validate before
  allowing "Next".
- `on_leave_back()` -- usually a no-op.
- `is_final_page()` -- only the curvature page returns `True` (changes
  the Next button's label to "Finish").

Page controllers receive `ui` (the loaded `.ui` widget, accessed via
`slicer.util.childWidgetVariables()` so `self.ui.someButton` works) and
`state` (the shared `WizardState`).

### `EarReconstructionPlanner.py` (main module file)
**Critical architecture point, already hit and fixed once**: page
controller modules are imported **lazily**, only the first time a page is
actually navigated to (`_get_or_create_controller()`), NOT all upfront at
widget construction time. This was a real bug that broke the first working
version: importing all 10 pages' modules eagerly at startup meant pages
needing `trimesh`/`SimpleITK` (e.g. `page_scutum_review.py` via
`core/mesh_export.py`) were imported before the Setup page ever got a
chance to install those packages, causing `ModuleNotFoundError` even
though the Setup page "worked." The `.ui` files for all pages ARE still
loaded upfront in `_build_pages()` (cheap, no Python deps involved, just
Qt widget construction) -- only the page controller *Python modules* are
deferred.

### Landmark placement pattern (scutum + pinna)
Uses Slicer's own Markups fiducial placement (`vtkMRMLMarkupsFiducialNode`,
`PointPositionDefinedEvent` observer), not a custom picking widget --
surgeons already know this interaction from other Slicer tools. Points are
captured one at a time with a "Place Point" button per step, with the
current step's plain-language instruction shown above it (source:
`core/landmarks.LANDMARK_STEPS` / `core/pinna_landmarks.PINNA_LANDMARK_STEP`).

### Review pages pattern (scutum + pinna)
Both follow the same shape: threshold slider(s) (`ctkSliderWidget`, tied
to `config.py` ranges) -> "Run Segmentation" button -> pulls the Slicer
volume node to SimpleITK (`sitkUtils.PullVolumeFromSlicer`) -> **coarse
crop first** (see gotcha above) -> build precise ROI mask ->
`segment_dl.segment()` (Stage A today, would-be Stage B later) ->
`postprocess` -> push result back as a Slicer labelmap node (for
slice/3D view + Segment Editor) AND export + load as a model node
(needed for the next page's surface-constrained drawing). "Open Segment
Editor" button hands off to Slicer's own built-in tool for manual
touch-ups rather than reinventing painting/erasing.

### Draw pages pattern (scutum + pinna)
Both follow the same shape: "Start Outline" creates a
`vtkMRMLMarkupsClosedCurveNode` with
`SetAndObserveSurfaceConstraintNode(model_node)` so clicks snap onto the
mesh surface (**flagged as NOT YET CONFIRMED against a real Slicer
install** -- this is the correct API for recent Slicer 5.x per available
documentation, but hasn't been runtime-tested; if it throws an
AttributeError, that's the first thing to check/fix) -> "Mark Inside
Point" places one fiducial seed point -> "Isolate Patch" collects the
curve's control points, loads the mesh via `trimesh.load()`, calls
`core.mesh_isolate.snap_points_to_vertices()` +
`isolate_surface_patch()`, exports the result, loads it as a new model.

### Verify page
Simple two-checkbox gate (`surgeon_approved_scutum`,
`surgeon_approved_pinna`) -- deliberately a manual, explicit confirmation
step before the final comparison runs, not just a formality.

---

## Curvature Project v4 integration (`curvature_integration.py`)

**Now runs entirely in-process inside Slicer's own Python -- no
subprocess, no separate venv, no separate Python install needed.** This
replaced an earlier, previously-working subprocess architecture (see
"Dead ends deliberately avoided" below for that architecture and exactly
why it was replaced, not just patched) once it became clear the three
dependencies forcing it (`pymeshlab`, `potpourri3d`, `open3d`) could each
be swapped for equivalents built on packages this extension already
requires (`numpy`, `scipy`, `trimesh`) for its own segmentation pipeline.
That's a real constraint win: a surgeon installing this extension no
longer needs Python installed system-wide at all, just Slicer itself plus
this extension's one-time Setup-page install (`dependencies.py`) --
exactly the "zero Python experience required" bar this whole project is
held to.

### Where the actual algorithm lives
`core/curvature/` (see the `core/` module reference above) is a
file-for-file port of the standalone Curvature Project v4's `main.py` +
`src/*.py`. It mirrors that project's own module boundaries deliberately,
so the two stay easy to cross-reference if Curvature Project v4 itself is
ever updated:

| Original (`Curvature Project v4/src/`) | Port (`core/curvature/`) | What changed |
|---|---|---|
| `mesh_io.py` | `mesh_io.py` | nothing -- pure trimesh load/cleanup |
| `signature.py`, `scoring.py`, `candidates.py`, `footprint.py` | same names | nothing -- pure numpy/scipy/trimesh already |
| `descriptors.py` | `descriptors.py` | pymeshlab's curvature filter -> `trimesh.curvature`'s discrete Gaussian/mean curvature measures, normalized by ball area, same shape-index/curvedness formulas (Koenderink & van Doorn) |
| `geodesics.py` | `geodesics.py` | potpourri3d's heat-method solver -> `scipy.sparse.csgraph.dijkstra` over the mesh's own edge graph (graph-shortest-path approximation to geodesic distance) |
| `registration.py` | `registration.py` | open3d's point-to-plane ICP -> `trimesh.registration.icp` (point-to-point, `reflection=False, scale=False` to stay a rigid transform); Chamfer/Hausdorff already used plain `cKDTree` even in the original |
| `heatmap.py` | `heatmap.py` | matplotlib's RdYlGn colormap -> a hand-rolled 3-stop lerp using the same ColorBrewer RGB stops matplotlib's RdYlGn is built from |
| `main.py` | `pipeline.py` | turned into a callable `run(scutum_path, pinna_path, output_dir, progress_callback)` instead of a script reading fixed `data/`/`output/` paths; its tunables (candidate count, histogram bins, score weights, etc.) moved into `config.py` |

Two real, deliberate accuracy trade-offs from this port, worth knowing
about if a surgeon ever asks "why did it suggest that site":
- **Geodesic distances are graph-shortest-path, not true continuous
  geodesics.** They read systematically a little *longer* than the real
  distance (travel is constrained to existing mesh edges), but this
  applies identically to the defect and every candidate, so the relative
  comparison the scoring depends on should still be meaningful.
- **ICP refinement is point-to-point, not point-to-plane.** Point-to-plane
  (the original's open3d choice) typically converges a bit faster/more
  robustly on smooth surfaces; point-to-point is still a completely
  standard alternative, and this stage was already designed to be "good
  enough given the coarse filter already found plausible candidates," not
  a from-scratch global registration pipeline.

One dependency wrinkle found (and fixed) while porting `descriptors.py`:
trimesh's own `discrete_mean_curvature_measure()` internally needs the
optional `rtree` package (an R-tree spatial index), which isn't in
`dependencies.py`'s required-package list and would have been a genuinely
new install -- the one place this port could have quietly broken its own
"zero new dependencies" goal. Fixed by reimplementing that one function's
edge-lookup step (`_mean_curvature_measure()` in `descriptors.py`) using a
`scipy.spatial.cKDTree` over edge midpoints instead of trimesh's R-tree,
reusing trimesh's own public `line_ball_intersection()` helper for the
actual geometry -- keeps the port to a strict zero-new-dependency change.
**If a future trimesh upgrade changes this internal behavior, that's the
first place to check.**

### Verification done so far (synthetic, no Slicer needed -- see "Testing approach")
Following this project's usual pattern of validating `core/` logic with
synthetic meshes before trusting it on real anatomy:
1. **Sphere curvature sanity check**: an icosphere has known analytic
   curvature (K = 1/r², H = 1/r everywhere) -- `descriptors.py`'s
   normalization was confirmed to recover this closely (mean shape_index
   ≈ 0.96 vs expected 1.0, mean curvedness ≈ 0.102 vs expected 0.100).
2. **Scoring discrimination check**: given *consistently constructed*
   patches (same extraction radius, same isolated single-bump context),
   `coarse_score` correctly scores a same-shape bump near zero and
   differently-shaped bumps clearly worse, ordered sensibly by how
   different they actually are.
3. **End-to-end pipeline smoke test**: a synthetic multi-bump "pinna" +
   disc-shaped synthetic "defect" run through `pipeline.run()` completes
   without error and produces valid `pinna_heatmap.ply` +
   `top_harvest_sites.csv`.

**What this did NOT end up validating**: an early version of check 3 also
asserted that, of three differently-scaled bumps scattered across the
synthetic pinna, the one matching the defect's shape always comes out
ranked #1 by the full 300-candidate pipeline. That assertion turned out to
be unreliable in this specific synthetic scene, for two identified,
explainable reasons that are properties of the *original* algorithm's
design (present before this port too, not introduced by it):
`CURVATURE_PATCH_RADIUS_MARGIN` (1.15) deliberately makes every candidate
patch ~15% larger than the defect's own measured radius, which creates a
real radial-distance-histogram mismatch against a defect whose boundary is
a sharp, clean disc cutoff (real, textured anatomy is much less likely to
expose this as sharply); and the shared curvedness histogram range is set
from the whole pinna's own 95th-percentile curvedness, which swings a lot
on a tiny synthetic pinna with only a few, very differently-scaled bumps.
Check 2 above isolates the thing that actually mattered for this port
(does `coarse_score` prefer a genuinely matching shape when compared
consistently) and passes cleanly -- but "does the full pipeline always
rank the single best real-anatomy site #1" is still, as it always was, an
open question that only real surgical cases can really answer.

### Still true from before (unconfirmed Slicer-side UI, not re-verified by this port)
`page_curvature.py`/`page_curvature.ui`'s live progress streaming into
`progressTextEdit`, `resultsTableWidget`'s population from
`top_harvest_sites.csv`, and the loaded heatmap model's
`SetScalarVisibility(True)` + `SetActiveScalarName(...)` display-node fix
(so the baked-in per-vertex red/yellow/green colors actually render
instead of flat gray) are all still **flagged as NOT YET CONFIRMED against
a real Slicer install** -- this port changes what runs underneath
`curvature_integration.run_curvature_comparison()` but doesn't touch, and
doesn't newly confirm, that Qt-facing layer. If the heatmap loads as flat
gray, or the progress log / results table don't populate, start there,
same as before. A `self._running` reentrancy guard on `CurvaturePage`
still stops a duplicate-connected Run button from launching two
concurrent comparison runs against the same scratch output folder (now
academic in practice, since a synchronous in-process call can't actually
overlap with itself the way two subprocesses could -- kept anyway as
cheap defense-in-depth).

### What the comparison actually does (for context, unchanged by the port)
Loads scutum defect + pinna meshes -> computes curvature descriptors
(shape index, curvedness) on both -> builds the defect's "signature"
(curvature histograms + an intrinsic geodesic radial-distance-from-center
histogram, weighted 1.5x vs 1.0x for extrinsic terms, motivated by
"cartilage bends freely but resists stretching") -> generates ~300
candidate harvest sites spread across the pinna -> coarse-scores every
candidate patch against the defect signature -> refines the top 15 with
local ICP alignment (Chamfer/Hausdorff distance) -> builds a full
per-vertex heatmap, highlighting the top 3 sites with the defect's actual
projected footprint shape (not a circle) -> exports `pinna_heatmap.ply`
(colored mesh, red-yellow-green, green = best) and
`top_harvest_sites.csv` (ranked list).

---

## Known issues / things to verify next in Claude Code

1. **"Reset All Points" button** on the scutum landmarks page was reported
   as not working. This was flagged right as the conversation ran out of
   usage and was NOT yet debugged -- **start here**. Check
   `_on_reset_clicked` in `page_scutum_landmarks.py`: it calls
   `self._fiducial_node.RemoveAllControlPoints()`, resets
   `self._current_step` to 0, and replaces `self.state.scutum_landmarks`
   with a fresh `EarCanalLandmarks()`, then calls `_update_step_display()`.
   Everything up to (and including) the threshold/segmentation stage was
   confirmed working, so the bug is localized to this one button/method.
2. **RESOLVED: `SetAndObserveSurfaceConstraintNode` alone doesn't
   constrain anything.** First real-Slicer test of the draw pages: the
   surgeon drew a ~30-point outline and placed a seed point, but "Isolate
   Patch" failed with `mesh_isolate.isolate_surface_patch`'s "needs at
   least 3 distinct points" error, despite ~30 points having been placed.
   Verified against Slicer's own `vtkMRMLMarkupsCurveNode` API docs:
   `SetAndObserveSurfaceConstraintNode(modelNode)` only *registers* the
   model node -- it has no effect on point placement unless the curve's
   `CurveType` is also set to `ShortestDistanceOnSurface`. The convenience
   method `SetCurveTypeToShortestDistanceOnSurface(modelNode)` sets both
   in one call and is the correct API; the old code was calling the
   lower-level method without ever setting the curve type, so the curve
   was never actually surface-constrained. Fixed in both
   `page_scutum_draw.py` and `page_pinna_draw.py`. Also improved
   `mesh_isolate.isolate_surface_patch`'s error message to report how
   many distinct vertices were actually found vs. how many points were
   drawn, so a future failure (e.g. genuinely too few points, or points
   clicked very close together) is easier to diagnose than the old
   generic message.
3. **`ctkSliderWidget` property names** (`.value`, `.minimum`, `.maximum`)
   -- used in both review pages, confirmed working in practice (Thomas
   got past the scutum review/segmentation stage with default -300/300
   threshold values).
4. **The curvature comparison now runs in-process** (`core/curvature/`,
   see "Curvature Project v4 integration" above) instead of as a
   subprocess against the standalone project's venv -- verified with
   synthetic meshes (sphere curvature check, scoring discrimination
   check, end-to-end smoke test), not yet against real anatomy. What's
   still unconfirmed is the same Slicer-side UI plumbing as before the
   port: `page_curvature.py`'s live progress streaming into
   `progressTextEdit`, the vertex-color display fix on the loaded heatmap
   model, and the ranked-candidates table population have not been
   runtime-tested in real Slicer (no Slicer install in the dev
   environment this was built in). If the heatmap loads but renders flat
   gray instead of colored, or the progress log / results table don't
   populate, start there.
5. **No `CMakeLists.txt`/`.s4ext`** -- this is a scripted module for
   development/testing only, loaded via Application Settings > Modules >
   Additional module paths, NOT via Extension Wizard (Thomas tried
   Extension Wizard first; it's the wrong tool for a scripted module
   without build files -- this was clarified but no packaging scaffold
   has been built, since it's not needed for solo testing).
6. **Slicer reportedly crashes opening the module before any DICOM volume
   is loaded** -- reported by Thomas, not yet reproduced/confirmed with an
   actual crash log (no Slicer install in the dev environment). Reviewed
   every code path reachable before/at the Setup and DICOM-load pages
   (`EarReconstructionPlanner.py`'s `setup()`/`_build_pages()`,
   `page_setup.py`, `page_dicom_load.py`, `dependencies.py`, and all 10
   `.ui` files) and found no Python-level code that touches
   `state.volume_node` or assumes a volume exists that early -- the only
   scene-bound custom widget anywhere is `volumeSelector`
   (`qMRMLNodeComboBox` in `page_dicom_load.ui`), and it isn't given a
   scene until its own page's `on_enter()` runs (lazy). Two speculative
   fixes applied without a confirmed root cause: (1) the top-level
   `qMRMLWidget` loaded in `EarReconstructionPlannerWidget.setup()` was
   never explicitly given `slicer.mrmlScene` -- a known gotcha for
   `.ui`-based scripted modules -- now fixed with
   `top_level_widget.setMRMLScene(slicer.mrmlScene)`. (2)
   `page_dicom_load.py`'s `on_enter()` now checks
   `slicer.mrmlScene.GetNodesByClass("vtkMRMLScalarVolumeNode")` and shows
   a plain-language "No volumes loaded yet" warning in `statusLabel`
   instead of a blank page when the scene has none. **If the crash
   persists after these two changes, the next step is getting an actual
   crash log/traceback from Thomas's machine** (Slicer's error log under
   Help > Report a bug, or the app log file) -- this is a case where
   guessing further without real output risks chasing the wrong thing.
   **Update:** not mentioned again despite many subsequent sessions of
   heavy module use (which all necessarily open the module first) --
   probably fixed, but never explicitly reconfirmed by Thomas. If it
   resurfaces, that's a signal the speculative fixes weren't the actual
   cause.

---

## Bugs already found and fixed (chronological, so the same mistake isn't reintroduced)

1. **`config.py` misplaced inside `core/` instead of next to it.**
   `EarReconstructionPlannerLib/__init__.py` adds its OWN directory to
   `sys.path` so `core/*.py`'s `from config import ...` statements resolve
   as top-level imports. `config.py` must live directly in
   `EarReconstructionPlannerLib/`, NOT inside `core/`. (First real-Slicer
   test failure: `ModuleNotFoundError: No module named 'config'`.)
2. **Eager page imports breaking the Setup page's entire purpose.**
   Fixed by making `EarReconstructionPlanner.py`'s `_build_pages()` only
   load `.ui` files upfront; actual page controller Python modules are
   imported lazily via `_get_or_create_controller()`, the first time each
   page is shown. (Second real-Slicer test failure:
   `ModuleNotFoundError: No module named 'trimesh'`, thrown from
   `page_scutum_review.py` during initial module construction, before the
   user had even reached the Setup page's install button.)
3. **`build_roi_mask()`/`build_spherical_roi_mask()` allocating a
   full-volume coordinate grid.** Both evaluate every voxel in whatever
   image they're given; fine on tiny synthetic test volumes, but a
   real full-resolution CT resampled to 0.3mm isotropic spacing is
   hundreds of millions of voxels. Fixed by adding
   `crop_to_physical_bounds()` / `crop_to_landmark_region()` /
   `crop_to_point_region()` to `roi_crop.py` -- cheap, corner-math-only
   rectangular crops that MUST run before either mask-building function is
   called on a real scan. (Third real-Slicer test failure:
   `numpy.core._exceptions._ArrayMemoryError: Unable to allocate 2.66 GiB`.)
   Margin tuned to 15mm default after confirming correctness first (30mm
   worked but took ~12s per run; 15mm brought it under 1s on an equivalent
   synthetic test).
4. **Isotropic resampling removed from the live pipeline.**
   `page_dicom_load.py` no longer calls `io_utils.resample_to_isotropic()`
   on the full scan. Reasoning (recorded in that file's module docstring):
   every downstream step already reads physical spacing/origin/direction
   off the image and works correctly on anisotropic voxels, so forcing
   the whole scan to a fine isotropic spacing (e.g. 0.3mm) up front was
   pure interpolation with no new information, and was making bone edges
   look blurred/less defined in the 3D view. `resample_to_isotropic()`
   itself is kept in `io_utils.py` (unused) in case it's ever useful for
   normalizing banked training cases. Nothing else needed to change --
   `roi_crop.py`/`segment_threshold.py`/etc. were already written in
   terms of physical mm, not voxel counts.
5. **`build_roi_mask()`'s inner clipping plane had an inverted normal.**
   In `roi_crop.py`, the truncated-cylinder ROI is bounded by two planes,
   one at `canal_opening` (the "outer" plane) and one at `near_eardrum`
   (the "inner" plane). The outer plane was built as
   `_unit(canal_axis_start - outer_normal_pt)` (own point minus its
   reference point, per the function's own documented rule: "normal
   direction from the paired reference landmark toward it"), but the
   inner plane had the operands swapped:
   `_unit(inner_normal_pt - canal_axis_end)` (reference minus own point --
   backwards). Net effect: instead of keeping the region between
   `canal_opening` and `near_eardrum`, the two planes' intersection kept
   only the region *past* `near_eardrum` (deeper than the eardrum
   landmark) and excluded the actual ear canal entirely. Symptom: Stage A
   segmentation reports "No bone wall found with these settings"
   regardless of threshold slider values, because the air-lumen scaffold
   step never sees the real canal lumen -- not a landmark-placement
   mistake on the surgeon's part. Fixed by flipping the inner plane to
   match the outer plane's convention:
   `_unit(canal_axis_end - inner_normal_pt)`.
6. **`build_roi_mask()` crashed with a raw `IndexError` when the ROI came
   out empty.** Surfaced immediately after fixing bug #5 above: with the
   plane sign corrected, a landmark set where `reference_outer` and
   `reference_inner` are placed on the wrong side (e.g. accidentally
   swapped) now makes the two clipping planes contradict each other,
   producing a completely empty ROI mask -- `crop_to_roi_bounding_box()`
   then called `LabelStatisticsImageFilter.GetBoundingBox(1)` for a label
   that doesn't exist, returning an empty tuple and crashing on
   `bbox[0]`. Fixed two ways: (1) `build_roi_mask()` now explicitly
   checks `inside_roi.any()` and raises a clear `ValueError` naming the
   likely cause (reference points on the wrong side) instead of letting
   the empty mask propagate into a cryptic downstream crash; (2)
   `EarCanalLandmarks.validate()` now proactively checks that
   `reference_outer` projects to the *outward* side of `canal_opening`
   along the canal axis, and `reference_inner` projects to the *inward*
   side of `near_eardrum`, so a wrong-side/swapped placement is caught
   right after landmark placement with a plain-language message, before
   the surgeon ever reaches Run Segmentation. `page_scutum_review.py`
   also now catches this `ValueError` from `build_roi_mask()` and shows
   it on `statusLabel` rather than crashing, as a second line of defense.
7. **Bugs #5 and #6 above were symptoms of a design problem, not just
   code bugs -- the whole 4-point tilted-plane ROI was replaced.** Even
   after fixing the sign error, real-world testing showed the
   wrong-side/swapped-reference-point warning from bug #6's `validate()`
   fix kept firing on placements that were, by the surgeon's own
   judgement, correct (`reference_outer` closer to `canal_opening`,
   `reference_inner` closer to `near_eardrum`). Root cause: the reference
   points only needed to be "roughly in line, doesn't need to be
   precise," but the plane math required them to land precisely on one
   side of a perpendicular-ish plane -- any small, perfectly reasonable
   imprecision could flip the sign. Rather than keep patching validation
   tolerances, the whole 4-point design was replaced with a simpler
   2-point one -- see "4-point tilted-plane ear canal ROI" in "Dead ends
   deliberately avoided" below for the design and why it's better. This
   removed `reference_outer`/`reference_inner` from `EarCanalLandmarks`
   entirely (`landmarks.py`, `roi_crop.py`), added
   `ROI_AXIAL_MARGIN_MM` to `config.py`, and dropped the scutum landmarks
   wizard step from 4 clicks to 2.
8. **The real root cause of "No bone wall found" (even after fixing bugs
   #5-#7): `sitkUtils.PullVolumeFromSlicer()` silently returns the volume
   in a different coordinate convention than the landmarks.** Confirmed
   from Slicer's own `Base/Python/sitkUtils.py` source:
   `PullVolumeFromSlicer()` converts the volume node's geometry from
   Slicer's native RAS convention to plain ITK/DICOM LPS convention
   (`ijkToLPS = rasToLps * ijkToRAS`, i.e. negating X and Y) before
   building the `sitk.Image`; `PushVolumeToSlicer()` converts back. But
   every landmark in this project (`canal_opening`, `near_eardrum`,
   `ear_center`) is captured directly from Slicer's Markups nodes
   (`GetNthControlPointPositionWorld`) in RAS, and nothing anywhere
   compensated for the mismatch -- `roi_crop.py`'s cropping and cylinder
   math, and `segment_threshold.py`'s axis-distance component selection,
   were all silently combining RAS landmark coordinates with an
   LPS-oriented image. Net effect: every ROI was built mirrored across
   the sagittal AND coronal planes from where the surgeon actually
   clicked -- so even with a correct 2-point ROI (bug #7's fix) and a
   correct threshold, the cylinder simply wasn't looking at the ear canal
   at all, which is why sweeping the entire air/bone threshold range
   never found anything. This explains the persistent failure much more
   fundamentally than bugs #5-#7 -- those were real bugs, but this one
   would have caused segmentation to fail (or succeed only by
   coincidence, e.g. on a near-symmetric crop) regardless.

   Fixed with a new `io_utils.flip_ras_lps()` helper (self-inverse:
   negates X/Y on origin and direction) called in both
   `page_scutum_review.py` and `page_pinna_review.py`: immediately after
   `PullVolumeFromSlicer()` (LPS -> RAS, so all core/ math matches the
   RAS landmarks) and again on a copy right before `PushVolumeToSlicer()`
   (RAS -> LPS, since that function expects plain ITK convention and
   converts to RAS itself). Mesh export (`mesh_export.label_map_to_mesh`)
   uses the *unflipped*, RAS-consistent label map so exported mesh
   vertices are in RAS and align correctly when loaded back into Slicer
   via `slicer.util.loadModel()`. **Any future code that pulls a volume
   via `sitkUtils.PullVolumeFromSlicer()` and combines it with
   Slicer-native (RAS) points/landmarks must apply this same flip** --
   this is not specific to the scutum/pinna review pages, it's a general
   property of that Slicer API.

   Thomas's suggestion to consider using Segment Editor's built-in
   threshold effect (instead of/alongside the hand-rolled SimpleITK
   thresholding) was a reasonable simplification idea, but wouldn't have
   fixed this specific bug either way -- the problem was never which
   thresholding implementation was used, it was that the ROI was being
   built in the wrong physical location before any thresholding even
   happened. "Open Segment Editor" already exists on both review pages
   for manual touch-ups after Stage A runs; fully replacing Stage A's
   engine with Segment Editor's scripted effects would trade away
   `core/`'s "testable without launching Slicer" property (see the
   "Testing approach" section) for less custom code -- worth considering
   later, but a separate decision from this bug fix.
9. **`SetAndObserveSurfaceConstraintNode` alone doesn't constrain a curve
   to a surface.** First real-Slicer test of the draw pages: surgeon drew
   a ~30-point outline and placed a seed point, but "Isolate Patch" failed
   with `mesh_isolate.isolate_surface_patch`'s "needs at least 3 distinct
   points" error. Verified against Slicer's own `vtkMRMLMarkupsCurveNode`
   API docs: `SetAndObserveSurfaceConstraintNode(modelNode)` only
   registers the model node -- it has no effect on point placement unless
   the curve's `CurveType` is also set to `ShortestDistanceOnSurface`. The
   old code in `page_scutum_draw.py`/`page_pinna_draw.py` never set the
   curve type, so the curve was never actually surface-constrained and
   most of the drawn points weren't landing near real mesh vertices.
   Fixed by switching to the convenience method
   `SetCurveTypeToShortestDistanceOnSurface(modelNode)`, which sets both
   the curve type and the constraint node in one call. Also improved the
   "<3 distinct points" error to report the actual distinct-vs-drawn
   count, so a future occurrence of this message is easier to diagnose.
10. **Seed point rejected as "on the drawn outline" even when clicked
    dead center.** After fixing bug #9, drawing worked better, but
    `isolate_surface_patch`'s seed-vertex lookup searched for the nearest
    vertex across the *entire* mesh, including the loop's own vertices --
    on a coarse mesh where a small drawn loop encloses only a few
    interior vertices, the boundary vertices are often closer to any
    interior click than the true interior vertices are to each other, so
    a perfectly centered click could still resolve to a loop vertex and
    get rejected. Fixed by restricting the nearest-neighbor search to
    non-loop vertices only (`roi_crop.py` unaffected; change is in
    `core/mesh_isolate.py`) -- a genuinely-inside seed point can no longer
    be rejected for this reason. Left in place: a clear error if the loop
    encloses *zero* non-boundary vertices at all (too small for the
    mesh's resolution), and the existing >90%-of-mesh "gap in the loop"
    check.
11. **Scutum/pinna landmark points were still visible (and clickable) on
    top of the model while drawing.** Since the landmark fiducial nodes
    were only ever referenced by local variables inside their own page
    controllers, no other page could reach them to hide them. Added
    `scutum_landmarks_fiducial_node`/`pinna_landmarks_fiducial_node` to
    `WizardState` (set when each landmarks page creates its fiducial
    node), and hide that node's display (`GetDisplayNode().SetVisibility
    (False)`) right after the corresponding review page successfully
    generates its model -- landmarks reappear automatically if the
    surgeon navigates back to re-place them (each landmarks page's
    `on_enter()` re-asserts visibility).
12. **A second, independent RAS/LPS bug -- this time on the mesh file
    side, not the volume side.** After bugs #9-#10 were fixed, drawing
    still failed intermittently (points collapsing to too few distinct
    vertices, or a correctly-centered seed point getting rejected) even
    with a well-drawn, well-spread-out outline. The giveaway was a VTK
    console warning, easy to dismiss as noise: `vtkMRMLModelStorageNode
    ... does not contain coordinate system information. Using LPS.` This
    is Slicer telling you exactly what it's doing: STL (and most generic
    mesh formats) have no field to record a coordinate system, so
    `slicer.util.loadModel()` **always assumes a plain STL's raw vertex
    numbers are in LPS** and flips them to RAS internally on load. But
    `mesh_export.export_mesh()` was writing files with vertices already
    in RAS (from `label_map_to_mesh()`, matching bug #8's fix) -- so
    Slicer's automatic LPS->RAS flip on load double-flipped them, meaning
    the model the surgeon actually sees and draws on in the 3D view was
    silently mirrored (X/Y negated) from the true anatomy. Meanwhile,
    `page_scutum_draw.py`/`page_pinna_draw.py`'s `_on_isolate_clicked`
    loaded the *same file* directly via `trimesh.load()` (bypassing
    Slicer's flip entirely), getting the true, unmirrored RAS vertices.
    So `curve_points`/`seed_point` (matching the mirrored, displayed
    model the surgeon clicked on) and `mesh.vertices` (matching the
    unmirrored raw file) were two different coordinate frames being
    compared directly -- explaining both the "<3 distinct points" and
    "seed landed on outline" symptoms independently of bugs #9/#10's
    causes, and independently of how carefully the surgeon drew.

    Fixed with a new `mesh_export.flip_ras_lps_points()` (self-inverse,
    negates X/Y of a point array -- the point-array equivalent of
    `io_utils.flip_ras_lps()`): `export_mesh()` now flips every mesh's
    vertices to LPS before writing, so `slicer.util.loadModel()`'s own
    flip lands the model correctly, in true RAS. Both draw pages now flip
    their `trimesh.load()`-ed mesh back to RAS immediately after loading
    (before any snapping/isolation math against RAS `curve_points`/
    `seed_point`), and now export the isolated patch via
    `mesh_export.export_mesh()` (which applies the same RAS->LPS flip)
    instead of calling `patch.export()` directly, which used to bypass
    the flip entirely. **This warning is otherwise harmless and expected
    for every STL Slicer loads** -- STL simply cannot embed coordinate
    system metadata, so Slicer will always print it; the bug was never
    the warning itself, only that nothing in this codebase was writing
    files consistent with what the warning describes Slicer doing.
    **General lesson for this project** (also true of bug #8): don't
    dismiss VTK/Slicer console warnings that describe an assumption being
    made about missing information -- Slicer is telling you exactly what
    convention it used, and it's only "noise" if every producer/consumer
    of that data actually agrees with that convention.
13. **`mesh_isolate.isolate_surface_patch()`'s flood-fill barrier had gaps
    even when the drawn curve was genuinely closed.** After bug #12 was
    fixed, the surgeon reported "the isolated region covers almost the
    entire mesh" despite being confident the loop was closed (it's a
    `vtkMRMLMarkupsClosedCurveNode`, after all). Root cause: the code
    only ever used the drawn curve's *control points* (~30 raw clicks,
    each snapped to its nearest mesh vertex) as the flood-fill barrier --
    but consecutive clicks are almost always several mesh edges apart
    (mesh vertex spacing is far finer than click spacing), so the barrier
    built from just those sparse points has real gaps in the mesh's
    vertex-adjacency graph for the fill to leak through, even though the
    *curve itself* looks fully closed on screen (Slicer's
    `ShortestDistanceOnSurface` curve type interpolates the actual path
    between clicks -- this code was never using that interpolated path,
    only the raw control points). Verified the mechanism with a synthetic
    20x20 grid-graph test: 4 sparse corner points as the barrier let a
    flood fill leak to 396/400 nodes; bridging consecutive points via
    shortest-path first correctly contained it to the 64 true interior
    nodes.

    Fixed by bridging every consecutive pair of drawn/snapped points --
    including the last point back to the first, closing the loop -- with
    the shortest path along the mesh's own vertex-adjacency graph
    (`nx.shortest_path`) before using them as the barrier. The "<3
    distinct points" check still runs first, against the raw (unbridged)
    snapped points, since a 2-point "loop" isn't fixable by bridging --
    there's no enclosed area between only 2 points regardless of the
    path between them. A new `ValueError` covers the (rare) case where
    two consecutive points aren't connected on the mesh surface at all
    (disconnected mesh components).

## Features added after the pipeline started working end-to-end

1. **Original models hidden once their isolated/cropped successor
   exists.** Same reasoning as the earlier landmark-visibility change:
   once `page_scutum_draw.py`/`page_pinna_draw.py` successfully isolate
   a patch, the *original* pre-isolation model
   (`scutum_bone_wall_model_node`/`pinna_region_model_node`) is now
   hidden (`GetDisplayNode().SetVisibility(False)`) so it doesn't overlap
   the newly isolated patch in the 3D view. Also added
   `scutum_defect_model_node`/`pinna_isolated_model_node` to
   `WizardState` and now remove-then-reload the isolated model node on
   repeat "Isolate Patch" clicks, instead of accumulating a new orphaned
   model node in the scene on every click (a latent bug this incidentally
   fixed -- `slicer.util.loadModel()` doesn't replace by path, it always
   creates a new node).
2. **Pinna patch auto-cropped toward the ear canal -- revised after the
   first version wasn't precise enough.** The first version of this
   feature (see the entry as originally written, superseded here) used
   `state.scutum_landmarks.canal_opening` directly as both the cut
   plane's *position* and *direction* source. In practice that position
   wasn't reliable: it was placed earlier, on a different mesh/context
   (the ear canal ROI, not this specific pinna geometry), and Thomas
   found it consistently wasn't far enough outward, leaving head-interior
   material attached. Fixed by decoupling position from direction:
   - `page_pinna_draw.py` now has a third marking step, **"Mark Canal
     Opening"**, alongside "Mark Inside Point" -- the surgeon places a
     fresh point directly on *this* pinna mesh, at the ear canal opening.
     `isolateButton` only enables once both the seed point and this new
     canal marker are placed (mirroring how the seed point alone used to
     gate it).
   - `mesh_isolate.crop_toward_canal(mesh, plane_point, axis_direction,
     margin_mm)` now takes the cut plane's position (`plane_point` --
     the new marker) and its direction (`axis_direction`) as separate
     arguments. `page_pinna_draw.py` passes the new marker as
     `plane_point`, but still derives `axis_direction` from
     `near_eardrum - canal_opening` (the ear canal's own landmarks) --
     that direction ("which way is into the head") is a stable
     anatomical fact independent of exactly where the plane needs to
     sit, so it didn't need replacing, only the position did.
   - New `mesh_isolate.keep_connected_component_containing(mesh,
     reference_point)`: Thomas's own suggestion -- a single plane cut
     doesn't always cleanly sever the head-interior material in one
     piece; it commonly leaves it as one or more islands disconnected
     from the main pinna body (since that material was only ever
     attached near the canal opening to begin with). This splits the
     mesh into connected components (`trimesh`'s own `.split()`) and
     keeps only the one containing/nearest to a known-good reference
     point -- the surgeon's own seed point, guaranteed to be on the
     pinna. Verified against a synthetic two-blob mesh (a small piece
     near the reference point + a larger, disconnected piece far away)
     that it picks the correct piece regardless of which one is bigger.
   - Order matters: `crop_toward_canal()` runs first (severs most of the
     interior material at the plane), then
     `keep_connected_component_containing()` cleans up whatever's left
     disconnected. Both are skipped gracefully (not an error) if the ear
     canal landmarks are missing -- unlikely given `PAGE_ORDER`, but
     fails safe.
   - `PINNA_CANAL_CROP_MARGIN_MM` (2.0mm default, `config.py`) is
     unchanged from the first version -- still shifts the cut plane
     outward from the marker so an imprecise click doesn't clip the
     pinna's own base tissue.
3. **3D view auto-recenters after segmentation.** Both review pages now
   call the same sequence a click on Slicer's own "center 3D view" button
   does (confirmed via Slicer's script repository docs):
   `slicer.app.layoutManager().threeDWidget(0).threeDView().resetFocalPoint()`
   followed by `.resetCamera()`. Runs right after the new model loads, so
   the surgeon sees it immediately instead of needing to manually
   pan/zoom to find it (this only re-frames the camera; it doesn't change
   any data).

---

## Dead ends deliberately avoided (context so they aren't re-suggested)

- **4-point tilted-plane ear canal ROI (`reference_outer`/
  `reference_inner`)**: the original design, directly adapted from
  Matin-Mann et al. (2025), used 2 extra landmarks (beyond
  `canal_opening`/`near_eardrum`) purely to let the ROI's two bounding
  planes tilt to match true anatomy instead of always being perpendicular
  to the canal axis. **Rejected/replaced** after it caused two real bugs
  in a row (an inverted plane normal, then a validation check that kept
  firing on reasonable placements) -- the root problem wasn't the specific
  bugs, it was that the design required a surgeon's "doesn't need to be
  precise" click to land precisely on one side of a plane. Since the ROI
  only ever needs to *comfortably contain* the canal (the real boundary
  comes from thresholding within it, not the ROI's shape), the tilt
  capability wasn't worth that fragility. Replaced with a 2-point design:
  planes perpendicular to the `canal_opening`->`near_eardrum` axis,
  extended by `ROI_AXIAL_MARGIN_MM`, with no surgeon-supplied direction to
  get wrong. **Don't reintroduce tilted/surgeon-oriented bounding planes
  for this ROI** unless there's concrete evidence the perpendicular
  approximation is clipping real anatomy -- prefer a bigger
  `ROI_AXIAL_MARGIN_MM` or `INITIAL_ROI_DIAMETER_MM` over adding more
  precision-dependent landmarks.
- **MedSAM2 for ear canal segmentation**: seriously considered (has an
  official Slicer plugin, zero-shot promptable segmentation, no training
  data needed), but **rejected** because Thomas's dev machine has a GTX
  1060 6GB, which is not realistically compatible with MedSAM2's CUDA/VRAM
  requirements. Reverted to the threshold-based Stage A approach, which
  needs no GPU at all. If hardware ever changes, this could be revisited,
  but don't suggest it as a default path.
- **nnU-Net for either ear canal or pinna**: was the original Stage B
  plan, still technically the `segment_dl.py` hook's intended eventual
  filler, but no training data has been banked yet (`MIN_CASES_TO_TRAIN =
  15` in `config.py`) and this hasn't been revisited since the MedSAM2
  detour. Not urgent.
- **Cartilage-specific CT segmentation for the pinna**: researched
  directly (searched published literature); confirmed no reliable
  CT-based approach exists (cartilage-vs-skin contrast is poor; published
  cartilage segmentation work is MRI-based). This is why the pinna's Stage
  A segments skin surface, not cartilage, and relies on the surgeon's
  drawn outline to do the real anatomical isolation.
- **Reproducing Curvature Project v4 inside Slicer's Python by installing
  its exact dependencies**: considered and rejected early on, due to the
  Python 3.12 venv / compiled-extension incompatibility risk. Don't
  suggest pip-installing `pymeshlab`/`potpourri3d`/`open3d` into Slicer's
  own Python as a fix -- **this is different from what was actually done
  later** (see the next entry): the comparison does now run inside
  Slicer's Python, just via a rewritten `core/curvature/` that never needs
  those three packages in the first place.
- **The subprocess/separate-venv architecture for Curvature Project v4
  itself**: this was the real, working, previously-documented design
  (`curvature_integration.py` shelling out to Curvature Project v4's own
  `.venv` `python.exe`, confirmed end-to-end multiple times) -- it wasn't
  a mistake, it was the correct call *at the time*, given the assumption
  that `pymeshlab`/`potpourri3d`/`open3d` were load-bearing and
  irreplaceable. It was deliberately replaced once it became clear each of
  those three could be swapped for a numpy/scipy/trimesh equivalent (see
  "Curvature Project v4 integration" above for the replacement table and
  why), because the subprocess/venv requirement was a direct violation of
  this project's "zero Python experience required" constraint -- a
  surgeon installing this extension shouldn't also need a separate Python
  install and a second project folder checked out next to it just to run
  the last wizard step. **Don't reintroduce the subprocess/venv bridge**
  thinking it's still necessary -- the standalone Curvature Project v4
  project (`C:\Users\Thomas James\Documents\Curvature Project v4\` on
  Thomas's machine) is now reference-only for this repo, same status as
  before, just no longer executed directly.

---

## Testing approach used so far (useful to continue in Claude Code)

Since `core/` has zero Slicer dependency, it was validated with synthetic
NumPy/SimpleITK volumes BEFORE ever touching Slicer or real scan data:
- A synthetic hollow tube (air core + bone shell + soft tissue) to verify
  `segment_bone_wall()` produces zero overlap between the exported wall
  and the internal air-lumen scaffold.
- A synthetic "head block + hemispherical ear bump" volume to verify the
  pinna threshold + `mesh_isolate` drawn-loop isolation correctly
  separates the bump from the flat head surface.
- A synthetic volume sized like a real full-resolution CT (850x850x700 @
  0.3mm) specifically to reproduce and then verify the fix for the
  `build_roi_mask()` memory blowup.
- Simulated Slicer's exact import chain (`sys.path` manipulation +
  `importlib.import_module`, mimicking what `EarReconstructionPlanner.py`
  does) to catch the lazy-import bug and the `config.py`-location bug
  without needing an actual Slicer install.

This pattern (write a throwaway synthetic-data test before/alongside any
new `core/` logic) caught every real bug so far and is worth continuing --
there is no Slicer installation available in the environment these were
developed in, only Thomas's own machine, so anything testable without
Slicer should be tested that way first, and real-Slicer console tracebacks
(pasted verbatim) have been the reliable way to catch what synthetic
testing can't.
