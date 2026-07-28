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
