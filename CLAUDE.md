# CLAUDE2.md

Condensed version of CLAUDE.md — same facts, fewer words, chronological
bug narratives compressed to their root cause + fix + lesson. Read
CLAUDE.md if you need the full blow-by-blow debugging history; read this
for the working context. Update whichever file the user asks you to
update.

---

## Project background

Thomas is a medical researcher/developer building surgical planning tools
for **microtia reconstruction** (scutum grafting using pinna cartilage).
Working developer, newer to Python, comfortable with 3D Slicer, Blender,
SimpleITK. Windows. Two merged sub-projects:

1. **Curvature Project v4** — standalone tool (own `.venv`, Python
   3.12.10) that compares a scutum defect mesh to a pinna mesh and
   heatmaps the best cartilage harvest site. Kept as a reference-only
   unedited copy at `C:\Users\Thomas James\Documents\Curvature Project v4\`;
   its algorithm has been **ported in-process** into this extension
   (`core/curvature/`).
2. **Ear Reconstruction Planner** (this repo) — a Slicer extension that
   generates the scutum defect mesh and pinna mesh from a CT scan
   (replacing a manual Slicer+Blender workflow), then runs the curvature
   comparison in-process.

**Priority above all else:** zero-Python-experience surgeon usability —
sliders not raw thresholds, drawing on 3D surfaces not typing
coordinates, one-time dependency install, plain-language instructions.

---

## Current status

Entire scutum and pinna pipelines confirmed working end-to-end in real
Slicer: DICOM load → scutum landmarks → pinna landmarks/review/draw →
scutum review/draw → (Verify, Curvature not yet runtime-tested).

**Not yet exercised in real Slicer:** Verify page; Curvature page's Qt
widgets (progress streaming, results table, heatmap vertex-color
display). The curvature *algorithm* itself (in-process port) is verified
against synthetic meshes, just not the Slicer-side UI plumbing around it.

**Open bug:** "Reset All Points" button on scutum landmarks page —
reported broken early, never actually debugged. First place to look if
Thomas reports a "reset" problem. See `_on_reset_clicked` in
`page_scutum_landmarks.py`.

**Probably fixed, unconfirmed:** Slicer crash opening the module before
a DICOM volume is loaded (Known Issues below) — two speculative fixes
applied, never recurred across many later sessions of heavy use.

---

## Architecture

`EarReconstructionPlanner/` scripted module (no CMake/compiled build
needed), a `QStackedWidget` wizard so the surgeon only ever sees one
plain-language step at a time:

```
0. Setup            0.5. Welcome (tutorial-mode only, skipped in Normal)
1. DICOM load
2. Scutum landmarks (2-point axis — stays here, see reorder note below)
3. Pinna landmarks (1-point + side)
4. Pinna review     5. Pinna draw
6. Scutum review     7. Scutum draw
8. Verify            9. Curvature (final page, "Finish")
```

**Pinna-first reorder:** pinna segmentation/draw runs before scutum
review/draw so the surgeon can iterate on scutum defect shape without
re-touching pinna state (`clear_downstream_state` is driven purely by
`PAGE_ORDER` position). Only the 2-point scutum *landmarks* page stayed
early (page 2) — `mesh_isolate.crop_toward_canal()` needs its axis
direction before pinna draw runs, and it's cheap enough (2 clicks, no
segmentation) not to bother moving. Each stage now explicitly
hides-on-entry / re-shows-on-return the other stage's leftover
models/landmarks (`page_pinna_landmarks.py`, `page_scutum_review.py`,
`page_scutum_draw.py`, `page_pinna_review.py`/`page_pinna_draw.py`,
`page_verify.py` all touch this) — **not yet runtime-tested**.

**Why `.ui` files + separate controllers:** Thomas wants to edit layout
in Qt Designer without touching Python. Every page's visuals live in
`Resources/UI/page_*.ui`; `EarReconstructionPlannerLib/pages/page_*.py`
wires logic by widget name only.

---

## Directory structure

```
EarReconstructionPlanner/
├── EarReconstructionPlanner.py       # module entry (ScriptedLoadableModule)
├── EarReconstructionPlannerLib/
│   ├── config.py                     # ALL tunables, plain-English comments
│   ├── dependencies.py               # pip_install for Setup page
│   ├── wizard_state.py               # WizardState dataclass + PAGE_ORDER (source of truth for page order)
│   ├── curvature_integration.py      # in-process bridge to core/curvature/
│   ├── core/                         # Slicer-independent, unit-testable
│   │   ├── io_utils.py               # DICOM/volume load; flip_ras_lps(); resample_to_isotropic() unused
│   │   ├── landmarks.py              # EarCanalLandmarks (2pt), validate()
│   │   ├── pinna_landmarks.py        # PinnaLandmarks (1pt + side)
│   │   ├── roi_crop.py               # ROI mask + MANDATORY coarse-crop-first (memory)
│   │   ├── segment_threshold.py      # Stage A bone-wall (ear canal)
│   │   ├── segment_pinna_threshold.py # Stage A skin-surface (pinna)
│   │   ├── segment_dl.py             # Stage B hook, falls back to Stage A (unimplemented)
│   │   ├── postprocess.py            # speck removal, hole fill, smoothing, close_small_tunnels()
│   │   ├── mesh_export.py            # label map -> trimesh; RAS<->LPS flip; fill_holes()
│   │   ├── mesh_isolate.py           # drawn-loop -> isolated patch; crop_toward_canal()
│   │   └── curvature/                # in-process port of Curvature Project v4
│   └── pages/                        # page_setup.py ... page_curvature.py, base_page.py
└── Resources/UI/                     # EarReconstructionPlanner.ui + page_*.ui
```

---

## `core/` reference

- **`config.py`** — every tunable constant, first stop for behavior
  tuning.
- **`landmarks.py`** — `EarCanalLandmarks`: `canal_opening` (bony
  ear-canal opening, not skin level), `near_eardrum`. Originally 4
  points; reduced to 2 (see Dead Ends).
- **`roi_crop.py`** — truncated-cylinder ROI from the 2 landmarks.
  **Must coarse-crop (`crop_to_landmark_region`/`crop_to_point_region`,
  ~15mm margin) before calling `build_roi_mask`/`build_spherical_roi_mask`**
  — both evaluate every voxel in the given image; skipping this OOMs on a
  real full-res CT (hit for real: tried to allocate 2.66 GiB).
- **`segment_threshold.py`** — segments the internal air lumen first
  (private, more reliable contrast), then dilates outward by
  `BONE_WALL_THICKNESS_MM` and thresholds for bone within that shell.
  Component selection: closest blob to the canal axis line.
- **`segment_dl.py`** — Stage B hook, currently a stub that always calls
  Stage A. MedSAM2/nnU-Net notes in Dead Ends.
- **`pinna_landmarks.py`** — `PinnaLandmarks`: just `ear_center` + side
  (never inferred).
- **`segment_pinna_threshold.py`** — segments outer **skin** surface
  (not cartilage — no reliable CT cartilage contrast exists, confirmed
  via literature search). Result is head+pinna still joined; drawing
  does the real isolation. Component selection is nearest-*voxel*
  distance (not center-of-mass — see bug #14). Includes
  `_remove_boundary_spike()` (morphological opening, physical mm radius)
  for a sphere-tangent-to-scalp cosmetic artifact.
- **`mesh_export.py`** — `label_map_to_mesh()` returns RAS mesh +
  `trimesh.repair.fill_holes()` (additive only, patches open boundaries).
  `export_mesh()` writes vertices flipped to LPS (STL has no coordinate
  frame; `slicer.util.loadModel()` always assumes LPS). Any code loading
  these files back via raw `trimesh.load()` must flip back to RAS.
- **`mesh_isolate.py`** — vertex-adjacency flood-fill from a seed point,
  drawn loop as barrier (loop vertices bridged via shortest-path so
  sparse clicks still form a closed barrier). Seed search excludes loop
  vertices. Raises `LoopDoesNotSeparateError` (distinct from generic
  input errors) when a >90%-reached fill suggests the mesh has a genus
  handle rather than a bad drawing. `crop_toward_canal()` — position and
  direction are separate args (direction from ear-canal landmarks,
  position from a surgeon-placed marker on the pinna mesh itself) — plus
  `keep_connected_component_containing()` for post-crop islands.
- **`postprocess.py`** — speck removal, hole fill, smoothing,
  `close_small_tunnels()` (closing-only, additive, `TUNNEL_CLOSING_RADIUS_MM`).
- **`curvature/`** — file-for-file port of Curvature Project v4's `src/`:
  `mesh_io/signature/scoring/candidates/footprint.py` unchanged (pure
  numpy/scipy/trimesh already); `descriptors.py` (pymeshlab →
  `trimesh.curvature`, with a hand-rolled `cKDTree` edge lookup to avoid
  pulling in `rtree`), `geodesics.py` (potpourri3d → scipy Dijkstra over
  mesh edges — graph-shortest-path, systematically a little long but
  consistent across all candidates), `registration.py` (open3d →
  `trimesh.registration.icp`, point-to-point not point-to-plane),
  `heatmap.py` (matplotlib RdYlGn → hand-rolled ColorBrewer lerp).
  `pipeline.py` is the callable entry point (ported from `main.py`).

---

## Wizard/Slicer-specific notes

- **Lazy page-controller imports**: `.ui` files load upfront; page
  controller *modules* import only on first navigation
  (`_get_or_create_controller()`). Required so Setup's dependency
  install can run before any page imports `trimesh`/SimpleITK-dependent
  code.
- **Landmark placement**: Slicer's own Markups fiducial nodes, one point
  per button click, plain-language step text from
  `landmarks.LANDMARK_STEPS`/`pinna_landmarks.PINNA_LANDMARK_STEP`.
- **Review pages**: slider(s) → Run → `PullVolumeFromSlicer` → coarse
  crop → precise ROI → `segment_dl.segment()` → `postprocess` → push back
  as labelmap + segmentation node (for Segment Editor) + exported model
  (for next page's surface-constrained drawing).
- **Draw pages**: Start Outline creates a `vtkMRMLMarkupsClosedCurveNode`
  via `SetCurveTypeToShortestDistanceOnSurface(modelNode)` (NOT
  `SetAndObserveSurfaceConstraintNode` alone — that only registers the
  node, doesn't constrain placement). Mark seed point → Isolate Patch →
  `mesh_isolate` → export → load as new model.
- **Verify page**: two explicit approval checkboxes, deliberate manual
  gate before the final comparison.
- **Tutorial mode**: `WizardState.tutorial_mode`, set once on Setup, read
  everywhere via `base_page.set_tutorial_text()`. Welcome page
  (tutorial-only) skipped in Normal mode via `wizard_state.SKIP_PAGE_IF`
  (a plain state predicate checked in `_show_page()`, so skip-checking
  never forces an early controller import).

---

## Curvature integration

Runs fully **in-process** inside Slicer's Python — no subprocess, no
separate venv (this replaced an earlier, genuinely-working subprocess
bridge once it was clear `pymeshlab`/`potpourri3d`/`open3d` could each be
swapped for numpy/scipy/trimesh equivalents; see table under `core/`
reference above). Verified so far only with synthetic meshes: sphere
curvature sanity check (recovers known K=1/r², H=1/r), scoring
discrimination check (passes), end-to-end pipeline smoke test (passes).
**Not validated**: whether the full 300-candidate pipeline always ranks
the true best real-anatomy site #1 — a known, explainable limitation of
the original algorithm's design (candidate-patch radius margin +
whole-pinna-relative histogram range), not introduced by the port.

**Still unconfirmed in real Slicer**: `page_curvature.py`'s progress
streaming, results table population, and the heatmap model's
`SetScalarVisibility`/`SetActiveScalarName` fix (needed or it renders
flat gray).

---

## Improving Canal Segmentation

Multi-session push to improve `segment_threshold.py` quality, motivated by
three concrete complaints: general precision on small/thin bone, the
scutum and malleus reading as fused (air gap between them mis-segmented as
bone), and missing wall chunks near the tympanic membrane. All new `core/`
logic in this section was verified with synthetic NumPy/SimpleITK scripts
in the scratchpad (not committed) before touching real Slicer, per the
existing Testing Approach below — but see the closing lesson: synthetic
tests validated *mechanisms* here without reliably predicting real-scan
outcomes, twice, in opposite directions.

**Shipped, confirmed working:**

- **Seed-based threshold calibration** (`core/threshold_seeds.py`,
  optional). Surgeon clicks 3 points (air lumen / bone / soft tissue);
  `calibrate_thresholds()` samples the same smoothed field thresholding
  itself sees and pre-fills the review page's air/bone sliders —
  `soft_tissue_seed` anchors both boundaries (air/tissue split and
  bone/tissue split). Sliders remain the actual source of truth, fully
  surgeon-adjustable after. Synthetic 150-patient experiment: mean Dice
  0.599→0.650, improvement roughly doubling under simulated scanner HU
  drift vs. a no-drift control — confirms it's correcting inter-scan
  calibration, not just averaging noise. Real-Slicer use showed calibrated
  `bone_threshold` routinely hitting the old slider ceiling (700) — real
  cortical bone HU legitimately runs higher than the old fixed-default-era
  range assumed — so `BONE_THRESHOLD_ADJUST_RANGE` was widened to
  `(-200, 2000)`.
- **Thin-wall warning** (`core/wall_quality.py`, advisory-only, never
  blocks Next). A second synthetic diagnostic (same 150-patient set,
  instrumented) found catastrophic segmentation failures do NOT correlate
  with air-lumen scaffold accuracy (r=-0.15) or noise (r=0.05) — only with
  true wall thickness (r=+0.83). This is a genuine partial-volume/
  resolution limit: no threshold choice, calibrated or not, can recover a
  wall thinner than the scan can resolve. `check_wall_thickness()`
  estimates local thickness via a distance-transform + local-max-filter
  approximation (not true sphere-fitting) and warns below
  `MIN_SAFE_WALL_THICKNESS_MM` (1.0mm) — flags risk, doesn't fix it.
- **Sub-voxel mesh extraction** (`mesh_export.label_map_to_mesh_subvoxel()`,
  scutum bone-wall pipeline only, NOT used for Segment-Editor-hand-edited
  masks). Extracts the isosurface from the real smoothed grayscale field
  near the boundary instead of the already-binarized mask, recovering
  genuine sub-voxel boundary position instead of snapping to the voxel
  grid. **Caught a real bug in review before shipping**: an early version
  let the sub-voxel blend partially undo `postprocess.close_small_tunnels()`
  (a postprocessing-closed tunnel's true intensity is below threshold by
  construction, so blending toward the true field there would silently
  reopen exactly the defect the 8-round "Isolate Patch" saga fixed). Fixed
  by requiring a `raw_threshold_mask` (pre-postprocess) argument and only
  blending where it agrees with the final mask; everywhere postprocessing
  changed something, hard-clamps to that decision instead. Verified via a
  synthetic thin-plate-with-tunnel test using `euler_number` (genus) as
  the signal, plus a stress test proving the guard (not just conservative
  defaults) is what prevents reopening. Real improvement is modest by
  design (~15-20% RMS boundary-error reduction in testing) — the same
  safety margin that protects against tunnel-reopening also damps the
  effect near the boundary; `SUBVOXEL_MESH_BAND_MM` is the documented
  lever if a stronger effect is ever wanted, at the cost of some of that
  safety margin.
- **Gaussian → CurvatureFlow smoothing swap** (`core/smoothing.py`,
  scutum/canal pipeline only — pinna pipeline deliberately left on plain
  Gaussian, already working, wasn't showing this problem). Root cause of
  the scutum/malleus fusion: the true air gap between them is thin enough
  that indiscriminate Gaussian blur pulls its smoothed intensity above
  `bone_threshold` before thresholding ever runs — not fixable by any
  threshold value after the fact, since the blurred data no longer
  contains the distinction. `CurvatureFlow` (edge-preserving diffusion —
  smooths within regions, doesn't diffuse across sharp transitions) was
  compared against plain Gaussian, a median filter, and a bilateral filter
  on synthetic data along two axes: noise suppression (spurious connected
  components surviving thresholding of a uniformly-noisy region right at
  the threshold value) and thin-gap preservation. CurvatureFlow was the
  only candidate matching Gaussian's noise suppression (~40 vs ~37
  residual components) while preserving a true 0.6mm gap almost exactly
  (-700 HU, vs. Gaussian's -128 — uncomfortably close to a typical
  `bone_threshold` around 100). **Confirmed fixed on a real scan** (direct
  A/B by Thomas) with no wall-quality regression — kept.
  `CURVATURE_FLOW_TIME_STEP=0.01`, `CURVATURE_FLOW_ITERATIONS=10`. One new
  shared module (`smoothing.smooth_for_thresholding()`) so
  `segment_threshold.py`/`threshold_seeds.py`/`mesh_export.py` can't drift
  out of sync on smoothing method, which they previously only did via a
  documented "must match" convention.

**Dead ends — don't re-suggest:**

- **Pure intensity-based bone segmentation (no shell restriction)**:
  briefly tried thresholding bone across the whole ROI instead of a fixed
  shell dilated from the selected air-lumen component, to catch irregular
  canal anatomy (septa, wider cross-sections) a single-component shell
  would silently miss. Reverted before real-Slicer testing at Thomas's
  request (not confirmed bad, just abandoned in favor of the epitympanum
  approach below) — the shell restriction is what stops the segmentation
  from grabbing the mastoid or ossicles, and removing it trades that
  safety for the irregular-anatomy coverage. Revisit only with an explicit
  decision to accept that tradeoff.
- **Epitympanum inclusion via `near_eardrum` reuse**: tried unioning a
  second air component (found within `EPITYMPANUM_SEARCH_RADIUS_MM`=8mm of
  the existing `near_eardrum` landmark) into the lumen scaffold before
  shell dilation, so the bone-wall shell would reach around the attic
  cavity where the scutum sits. Synthetic testing looked clean (correctly
  found a separate air pocket, correctly ignored distant unrelated ones).
  **Confirmed harmful on a real scan**: caused widespread real wall
  dropout (a strip down the anterior wall, a chunk of the posterior wall —
  not localized to the epitympanum region) without fixing the scutum/
  malleus fusion it targeted. Fully reverted, including inlining
  `_component_centroids_physical` back into `_closest_component_to_axis_line`
  once it lost its second caller. If revisited, use a dedicated 3rd
  landmark click placed directly inside the epitympanum instead of
  guessing proximity to `near_eardrum` — that landmark's own instruction
  text ("just outside the eardrum, at the inner end of the canal") never
  promised it was inside the attic recess.

**Still open**: the scutum/malleus fusion persists even with CurvatureFlow
smoothing. A direct synthetic test found neither Gaussian nor CurvatureFlow
preserves a true gap at exactly 1 voxel (0.3mm = `TARGET_VOXEL_SPACING_MM`)
— if the real gap is that thin, this is a genuine resolution limit no
denoising-method choice can fix. Next idea (not yet implemented, Thomas's
preferred next direction): a sheetness/tubeness (Hessian-eigenvalue-based)
enhancement filter run before thresholding — actively enhances thin-sheet
structure signal rather than just avoiding damaging it, a different
mechanism than anything tried so far.

**Lesson**: synthetic tests here validated real mechanisms (calibration
math, tunnel-reopening guard, noise-vs-gap-preservation tradeoffs) but
twice failed to predict real-scan outcomes in opposite directions —
epitympanum inclusion looked clean synthetically but damaged real walls;
CurvatureFlow's real fix was never fully reproduced synthetically either
(a flat-slab and a curved-tube test both showed zero measurable erosion
risk, so the synthetic evidence for adopting it was suggestive, not
proof). Real-scan A/B comparison by Thomas was what actually decided both
calls. Doesn't invalidate synthetic-first testing (it still caught a real
bug in sub-voxel meshing before it ever reached Slicer) — just confirms
the existing Testing Approach note below: synthetic tests catch what they
model, real-Slicer feedback is still the only way to catch what they
don't.

---

## Known open issues

1. **Reset All Points (scutum landmarks)** — reported broken, never
   debugged. Start here for any future "reset" report.
2. **No `.s4ext`/CMakeLists** — dev-only scripted module, loaded via
   Additional Module Paths, not Extension Wizard. No packaging scaffold
   built (not needed for solo testing yet).
3. **Pre-DICOM-load crash** — probably fixed (two speculative fixes,
   `setMRMLScene` call + no-volumes status message), never explicitly
   reconfirmed, hasn't recurred.
4. Several Slicer API calls are "written to be correct per docs, never
   runtime-confirmed": `SetCurveTypeToShortestDistanceOnSurface`,
   `ImportLabelmapToSegmentationNode`/`ExportVisibleSegmentsToLabelmapNode`,
   Segment Editor widget attribute names (`setSourceVolumeNode` vs
   `setMasterVolumeNode`), stage-hide/re-show visibility logic for the
   pinna-first reorder.

---

## Key lessons from past bugs (read before touching volume/mesh coordinate code)

- **RAS/LPS mismatch (general Slicer API gotcha, hit twice, two
  different places):** `sitkUtils.PullVolumeFromSlicer()` silently
  returns LPS while every landmark captured from Markups nodes is RAS —
  must `io_utils.flip_ras_lps()` right after pulling and again before
  pushing back. Separately, `slicer.util.loadModel()` always assumes a
  loaded STL's raw vertices are LPS (STL has no coordinate-frame field)
  — `mesh_export.py` must write LPS-flipped vertices, and any code that
  bypasses Slicer and loads the same file via raw `trimesh.load()` must
  flip back to RAS itself. **Any new code touching `sitkUtils` or
  `slicer.util.loadModel()` alongside Slicer-native points must handle
  this.** General lesson: don't dismiss VTK/Slicer console warnings
  about assumed coordinate systems — they say exactly what convention is
  being used.
- **Curve surface constraint**: `SetCurveTypeToShortestDistanceOnSurface(modelNode)`
  is required; `SetAndObserveSurfaceConstraintNode` alone does nothing.
- **Isolate Patch / mesh topology saga** (8 rounds, fully resolved
  2026-07-28): root cause was never smoothing-parameter tuning — it was
  (a) unconditional re-export/re-postprocess on every "Next" click
  eroding/re-holing an already-good mesh (fixed: gate reprocessing behind
  an explicit `_segmentation_edited` flag, only touched if the surgeon
  used Segment Editor — see [[feedback_no_silent_reprocessing]]), and (b)
  genuine topological handles (genus > 0) from the ear canal's own
  tunnel-like anatomy defeating the "closed loop separates the surface"
  assumption. Final fix, Thomas's own idea: run `crop_toward_canal()`
  **before** isolation instead of after, so canal-adjacent tunnels are
  severed (or discarded) before the flood-fill ever has to contend with
  them. `LoopDoesNotSeparateError` + a local-radius flood-fill
  restriction + a fallback-mesh retry all remain as defense-in-depth.
  **Lesson**: when a user gives a precise before/after observation
  ("watertight here, holed there"), trust it over further algorithmic
  theorizing — the correct fix (reprocessing gate) was tried, wrongly
  reverted on an unproven assumption, then correctly reapplied once
  direct evidence confirmed it.
- **Absolute point size**: Markups `GlyphScale` is a screen-percentage
  recomputed from a possibly-stale camera scale factor, so freshly
  created points render huge until something recomputes the camera.
  Fixed via `set_absolute_point_size()` (`UseGlyphScale(False)` + fixed
  mm `GlyphSize`) rather than forcing a recenter (which would also
  reorient the camera, discarding the angle the surgeon was told to set
  up first). Scutum and pinna need separate constants (different physical
  scales): `SCUTUM_DRAW_POINT_SIZE_MM = 0.3`, `PINNA_DRAW_POINT_SIZE_MM = 2.0`.
- **Segment Editor button was inert**: it edits `vtkMRMLSegmentationNode`s,
  not plain labelmaps — fixed by converting to a real segmentation node
  on Run, wiring `setSegmentationNode`/`setSourceVolumeNode` on open, and
  syncing edits back into the mesh on Next.
- **Memory blowup**: mask-building functions (`build_roi_mask`,
  `build_spherical_roi_mask`) must always be preceded by a coarse
  rectangular crop on real (non-synthetic) volumes.

---

## Dead ends — don't re-suggest

- **4-point tilted-plane ROI** (`reference_outer`/`reference_inner`,
  adapted from Matin-Mann et al. 2025): caused two real bugs because it
  required an imprecise surgeon click to land precisely on one side of a
  plane. Replaced with 2-point perpendicular-plane ROI. Don't reintroduce
  surgeon-oriented tilt landmarks — prefer a bigger `ROI_AXIAL_MARGIN_MM`
  if perpendicular planes ever clip real anatomy.
- **MedSAM2** for ear canal: rejected — Thomas's GTX 1060 6GB isn't
  viable for its CUDA/VRAM needs. Revisit only if hardware changes.
- **nnU-Net**: still the eventual `segment_dl.py` Stage B plan, but no
  training data banked yet (`MIN_CASES_TO_TRAIN = 15`). Not urgent.
- **Cartilage-specific CT segmentation**: confirmed via literature search
  that no reliable CT-based approach exists (MRI-only in published work).
  This is why pinna Stage A segments skin, not cartilage.
- **Installing Curvature Project v4's original deps (`pymeshlab`/
  `potpourri3d`/`open3d`) into Slicer's Python**: rejected early
  (Python 3.12 venv / compiled-extension risk) — superseded by the
  numpy/scipy/trimesh rewrite in `core/curvature/`, not by ever
  installing those packages into Slicer.
- **Subprocess/separate-venv bridge to standalone Curvature Project v4**:
  was genuinely working, not a mistake at the time, but violated the
  zero-Python-experience constraint (surgeon would've needed a second
  Python install). Deliberately replaced by the in-process port — don't
  reintroduce it.

---

## Testing approach

`core/` has zero Slicer dependency, so validate new logic with synthetic
NumPy/SimpleITK volumes and trimesh meshes first — this pattern (hollow
tube for bone-wall, head-block+bump for pinna isolation, full-res-sized
volume for the memory blowup, simulated Slicer import chain for the lazy-
import/config.py-location bugs) caught every bug in this project so far.
There is no Slicer install in the dev environment — real-Slicer console
tracebacks (pasted verbatim by Thomas) are the only way to catch what
synthetic testing can't, and WebSearching actual Slicer API source/docs
before trusting an API's assumed behavior has repeatedly found real bugs
(see [[feedback_verify_before_trusting_apis]]).
