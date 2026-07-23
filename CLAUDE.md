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
   graft harvest sites. This already works and is NOT part of this
   migration -- it stays as-is; see "Curvature Project v4 integration"
   below for how the extension calls it.

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

## High-level architecture

A single Slicer extension, `EarReconstructionPlanner/`, implemented as a
**scripted module** (no compiled C++, no CMake build step needed for
development -- see "Installing for testing" below). It's a 10-page wizard:

```
0. Setup              -- one-time Python dependency install
1. DICOM load         -- confirm which already-loaded scan to use
2. Scutum landmarks   -- place 4 points defining the ear canal axis
3. Scutum review      -- run/adjust bone-wall threshold segmentation
4. Scutum draw        -- surgeon draws the defect outline on the 3D mesh
5. Pinna landmarks    -- place 1 point + pick left/right ear
6. Pinna review       -- run/adjust skin-surface threshold segmentation
7. Pinna draw         -- surgeon draws the pinna outline on the 3D mesh
8. Verify             -- surgeon checkboxes confirming both meshes are correct
9. Curvature          -- runs Curvature Project v4 as a subprocess, loads heatmap
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
│   ├── curvature_integration.py          # subprocess bridge to Curvature Project v4
│   ├── core/                             # segmentation logic, Slicer-independent, unit-testable
│   │   ├── io_utils.py                   # DICOM/volume loading, isotropic resampling
│   │   ├── landmarks.py                  # EarCanalLandmarks (4-point), .fcsv/.json loaders
│   │   ├── pinna_landmarks.py            # PinnaLandmarks (1-point + side)
│   │   ├── roi_crop.py                   # ROI mask building + cropping (see gotchas below)
│   │   ├── segment_threshold.py          # Stage A: bone wall segmentation (ear canal)
│   │   ├── segment_pinna_threshold.py    # Stage A: skin surface segmentation (pinna)
│   │   ├── segment_dl.py                 # Stage B hook: trained model, falls back to Stage A
│   │   ├── postprocess.py                # speck removal, hole filling, smoothing
│   │   ├── mesh_export.py                # label map -> trimesh via marching cubes
│   │   └── mesh_isolate.py               # drawn-loop -> isolated mesh patch (shared logic)
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
- **`landmarks.py`**: `EarCanalLandmarks` dataclass, 4 points
  (`canal_opening`, `near_eardrum`, `reference_outer`, `reference_inner`).
  `validate()` does forgiving sanity checks (plausible canal length,
  points not coincident). `from_slicer_fcsv()` / `from_json()` were built
  for CLI testing before the Slicer wizard existed -- may not be needed
  going forward but harmless to keep.
- **`roi_crop.py`**: builds a truncated-cylinder ROI mask from the 4
  landmarks (`build_roi_mask`), matching the approach in Matin-Mann et al.
  (2025)'s external-ear-canal-implant segmentation paper (mean Dice 0.909
  using landmarks + threshold + connected-component cleanup, no ML
  needed). **Critical gotcha, already fixed once**: `build_roi_mask()`
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
  Much simpler than the ear canal's 4 points because Stage A here doesn't
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
  Laplacian smoothing, in physical (RAS mm) coordinates matching Slicer's
  own convention. Meshes do **not** need to be watertight (confirmed by
  Thomas -- the scutum/pinna outputs are open surface patches, not solids).
- **`mesh_isolate.py`**: the surgeon-drawn-outline -> isolated-patch logic,
  shared between the pinna and scutum draw stages (and originally
  motivated by the scutum "draw on the ceiling to mark the defect" idea).
  Approach: build a vertex-adjacency graph (networkx, mirroring the same
  dependency/mental model Curvature Project v4 already uses for its own
  geodesic patch extraction), flood-fill from a surgeon-provided seed
  point with the drawn loop's vertices acting as a barrier, include the
  loop vertices in the output for a clean boundary. Has safety checks:
  raises a clear error if the loop has <3 points, if the seed point lands
  on the loop itself, or if the fill reaches >90% of the mesh (usually
  means the loop has a gap and isn't actually closed). Tested successfully
  against a synthetic "head with ear bump" mesh.
- **`postprocess.py`**: speck removal (min volume threshold), hole
  filling, light morphological smoothing. Engine-agnostic (works on output
  from either Stage A or a future Stage B).
- **`io_utils.py`**: DICOM/volume loading (folder of .dcm or single
  NRRD/NIfTI/etc. file via SimpleITK), resampling to isotropic spacing.

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

**This is the most recently completed, most concrete part of the
integration** -- Thomas shared the real `main.py` from Curvature Project
v4, so this is no longer guesswork.

### The environment problem (important, already solved architecturally)
Curvature Project v4 runs in its own **Python 3.12.10 venv** with compiled
extensions (`pymeshlab`, `potpourri3d`, `open3d`). Slicer bundles its own
embedded Python (confirmed different: Thomas's error logs show Slicer
5.10.0's own bundled Python at
`C:\Users\Thomas James\AppData\Local\slicer.org\3D Slicer 5.10.0\lib\Python\...`).
Compiled-extension wheels are version-specific, so there's a real risk
those packages simply can't be installed into Slicer's Python at all.
**Decision: do not attempt to import Curvature Project v4 into Slicer's
Python process.** Instead, run it as a **separate subprocess** using its
own existing venv's `python.exe`, communicating only via files on disk.
This is the same pattern used by real Slicer plugins that depend on
incompatible environments (e.g. Docker-based/remote-server inference
backends, as seen in the ABL Temporal Bone Segmentation Slicer extension
researched earlier in this project).

### The actual interface (confirmed from real `main.py`, not guessed)
Critically, **`main.py` takes NO command-line arguments**. It reads:
- `Path("data") / "scutum.stl"` and `Path("data") / "pinna.stl"`,
  relative to wherever the *process* is run from (its working directory)
- writes `Path("output") / "pinna_heatmap.ply"` and
  `Path("output") / "top_harvest_sites.csv"`, same way
- its own internal imports (`from src.mesh_io import ...`) resolve based
  on `main.py`'s own file location (Python adds that to `sys.path`
  automatically), unaffected by working directory

So the integration is: create a per-case scratch folder, copy
`scutum_defect_mesh_path` -> `scratch/data/scutum.stl` and
`pinna_isolated_mesh_path` -> `scratch/data/pinna.stl` (exact filenames
required), run `[VENV_PYTHON_PATH, CURVATURE_PROJECT_MAIN_PATH]` with
`cwd=scratch_folder`, then read back
`scratch_folder/output/pinna_heatmap.ply` (and the CSV). **No changes to
Curvature Project v4 itself needed.**

### What's still a placeholder
Two path constants at the top of `curvature_integration.py`:
```python
VENV_PYTHON_PATH = r"C:\Users\Thomas James\Documents\Curvature Project v4\.venv\Scripts\python.exe"
CURVATURE_PROJECT_MAIN_PATH = r"C:\Users\Thomas James\Documents\Curvature Project v4\main.py"
```
`is_configured()` checks both exist; until they point to real files,
`page_curvature.py` shows "not connected yet" and disables the Run
button rather than erroring. **These need to be set to Thomas's actual
paths** -- worth checking first whether this has already been done.

### What Curvature Project v4's `main.py` actually does (for context)
Loads scutum defect + pinna meshes -> computes curvature descriptors
(shape index, curvedness via pymeshlab) on both -> builds the defect's
"signature" (curvature histograms + an intrinsic geodesic
radial-distance-from-center histogram, weighted 1.5x vs 1.0x for
extrinsic terms, motivated by "cartilage bends freely but resists
stretching") -> generates ~300 candidate harvest sites spread across the
pinna -> coarse-scores every candidate patch against the defect signature
-> refines the top 15 with local ICP alignment (Chamfer/Hausdorff
distance) -> builds a full per-vertex heatmap, highlighting the top 3
sites with the defect's actual projected footprint shape (not a circle)
-> exports `pinna_heatmap.ply` (colored mesh, red-yellow-green, green =
best) and `top_harvest_sites.csv` (ranked list). Depends on `pymeshlab`,
`potpourri3d`, `open3d`, `numpy`, `scipy`. Only `main.py` itself has been
shared so far -- `src/mesh_io.py`, `src/descriptors.py`, `src/geodesics.py`,
`src/signature.py`, `src/candidates.py`, `src/scoring.py`,
`src/registration.py`, `src/footprint.py`, `src/heatmap.py` exist in the
real project but haven't been reviewed in this chat.

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
2. **`SetAndObserveSurfaceConstraintNode`** (in `page_scutum_draw.py` and
   `page_pinna_draw.py`) -- correct Slicer 5.x Markups API per
   documentation, never runtime-tested yet (drawing pages haven't been
   reached in testing). First thing to check if curve drawing doesn't
   snap to the mesh surface correctly.
3. **`ctkSliderWidget` property names** (`.value`, `.minimum`, `.maximum`)
   -- used in both review pages, confirmed working in practice (Thomas
   got past the scutum review/segmentation stage with default -300/300
   threshold values).
4. **Curvature integration untested end-to-end** -- the subprocess
   mechanism has never actually been run against real Curvature Project
   v4 code (only reasoned about from reading `main.py`). First real run
   may surface issues (e.g. `main.py`'s other dependencies like
   `src/mesh_io.py`, `src/descriptors.py` etc. weren't reviewed -- only
   `main.py` itself was shared).
5. **No `CMakeLists.txt`/`.s4ext`** -- this is a scripted module for
   development/testing only, loaded via Application Settings > Modules >
   Additional module paths, NOT via Extension Wizard (Thomas tried
   Extension Wizard first; it's the wrong tool for a scripted module
   without build files -- this was clarified but no packaging scaffold
   has been built, since it's not needed for solo testing).

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

---

## Dead ends deliberately avoided (context so they aren't re-suggested)

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
- **Reproducing Curvature Project v4 inside Slicer's Python**: considered
  and rejected due to the Python 3.12 venv / compiled-extension
  incompatibility risk (see above). Don't suggest pip-installing
  `pymeshlab`/`potpourri3d`/`open3d` into Slicer's own Python as a fix.

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
