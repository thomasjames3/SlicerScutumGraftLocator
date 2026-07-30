# CLAUDE.md

Working context for this project: same facts as the original blow-by-blow
debugging history, condensed to root cause + fix + lesson per item (the
old, more verbose CLAUDE.md has been deleted; this is now the only copy).

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
8. Verify            9. Curvature
10. Complete (final page, "Finish" — download step, see below)
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

**UI rework (2026-07-29)**: removed the scrolling `progressTextEdit` log
box (Thomas: surgeons don't need to see a Python-terminal-style log) —
run progress now streams into the one-line `statusLabel` instead. Added
a "Locate" column to the results table: each row gets a Show
Point/Hide Point button that toggles one control point on a new
`state.harvest_site_markup_node` (one `vtkMRMLMarkupsFiducialNode`
shared across all rows, row index == control point index), labeled "1",
"2", etc. and colored magenta to stand out against the heatmap's
red/yellow/green/blue/black coloring. Point position comes from
`heatmap_model_node`'s own polydata (`GetPoint(vertex_id)`), not a
re-parse of the CSV's x/y/z strings, so it lands exactly on the
rendered mesh. Points start hidden and only toggle visibility (never
recreated) when a row's button is clicked. Harvest site point size
initially 3.0mm; Thomas found that too large on the heatmap and asked
for it halved to 1.5mm (`HARVEST_SITE_POINT_SIZE_MM` in config.py).

Also added an explanation of Chamfer vs. Hausdorff distance to this
page's tutorial text: Chamfer is the average post-alignment mismatch
across the whole candidate patch (main ranking number), Hausdorff is
the single worst mismatch anywhere on the patch (flags one bad local
spot worth checking in the 3D view before committing to that site).

**Download step split into new final page (2026-07-29)**: the curvature
page's "Download results" section moved to a new `page_complete.py`/
`page_complete.ui` (PAGE_ORDER entry `"complete"`, after `"curvature"`).
Curvature is no longer `is_final_page()`; Complete is. Download list
expanded from 2 files (heatmap + scutum defect) to 4 (adds pinna
isolated mesh + scutum bone wall mesh) per Thomas's request. Download
list is table-driven (`_DOWNLOADABLE_FILES` in `page_complete.py`:
checkbox widget name, WizardState field name, missing-file message), so
future downloadable files are one-line additions. Each checkbox auto-
disables if its file was never generated (field is `None`).

---

## Pinna segmentation performance (2026-07-29)

Thomas reported pinna segmentation "really slow" on a scan with much finer
native spacing than previously tested (0.173x0.173x0.2mm, vs. 0.5mm on a
prior scan) -- expected in principle, since neither the pinna nor scutum
pipeline resamples to a fixed spacing (deliberate design choice, see
"Improving Canal Segmentation" below).

**Root cause found and fixed**: `roi_crop.build_spherical_roi_mask()`/
`build_roi_mask()` built one full `(nx*ny*nz, 3)` physical-coordinate array
for the entire coarse-cropped box before evaluating the sphere/cylinder
test. At this scan's spacing, the pinna's ~110mm coarse-crop box is ~222M
voxels -- several GB of throwaway float64 arrays at once. A synthetic
benchmark (scratchpad, not committed) confirmed this wasn't just linear
slowness: the old whole-array approach scaled *worse* than linear with
voxel count (0.92s/8M vox -> 5.57s/27M -> 20.5s/64M, consistent with
memory-allocation/paging overhead), while a rewrite that loops over
z-slices (`roi_crop._build_mask_by_slices()`, shared by both functions,
keeps peak memory to one slice at a time instead of the whole volume)
scaled close to linear (0.62s -> 1.99s -> 4.71s at the same sizes) -- a
1.5x-4.4x speedup that *grows* with resolution, i.e. gets better exactly
where it's needed. Verified bit-for-bit identical output against the old
implementation on a small synthetic volume and one with a rotated
(oblique) direction matrix -- a pure performance change, not a behavior
change.

**Dead end, don't re-try**: also suspected `segment_pinna_threshold.
_remove_boundary_spike()`'s `BinaryMorphologicalOpening` (ball structuring
element, radius scales as `1.5mm/spacing` per axis -- `[9,9,8]` voxels at
this scan's spacing vs. `[3,3,3]` at 0.5mm) as a second bottleneck, since
non-separable ball-SE cost is textbook-expected to scale with kernel
*volume*. Tried replacing it with a distance-transform-based Euclidean
opening (erode via EDT >= radius, dilate via EDT-of-complement <= radius),
expecting radius-independent near-linear cost. **Directly benchmarked
before shipping (per this project's synthetic-test-first practice) and
the expectation was wrong**: at the real `[9,9,8]` radius, the EDT version
was 3-4x SLOWER than SimpleITK's own opening at real-scan-sized arrays
(14.1s vs. 4.7s at 27M voxels) and scaled worse, not better --
SimpleITK's default Ball kernel (confirmed faster than its Box kernel
option too, in the same benchmark) is already well-optimized here.
Reverted; `_remove_boundary_spike` is functionally unchanged from before
this session.

**Spike/roof-hole: confirmed fixed, mechanism NOT understood** -- Thomas
re-tested on the same scan after the roi_crop fix above and the spike
issue is gone ("spike removal worked"). This is a real, welcome result,
but be honest about it next session: `_remove_boundary_spike` itself was
reverted to byte-for-byte the same code it was before this session (the
EDT experiment above was undone), and the roi_crop rewrite was verified
bit-for-bit identical to the old grid-building on every synthetic case
tested (including a rotated direction matrix) -- so nothing that should
have changed the actual segmentation result, changed. Best working guess:
floating-point summation-order differences (the old code's `idx_grid @
direction.T` matmul vs. the new code's explicit per-axis broadcasted
adds) shifted which voxels land inside vs. outside the ROI sphere by a
handful of ULPs right at the exact tangent point where the sphere grazes
the scalp -- and because that graze is *already* a knife-edge geometric
coincidence (that's the whole reason this artifact exists), even a
sub-nanometer shift there could plausibly flip enough boundary voxels to
change whether the spike's neck survives the opening. Not verified, only
plausible -- if the spike ever comes back on a different scan, don't
assume this side effect will repeat.

**Now open: Stage A is still "quite slow" even after the roi_crop fix.**
This means there's at least one more real bottleneck beyond what this
session found and fixed -- most likely suspect, not yet measured: once
`crop_to_roi_bounding_box()` crops down to the ROI sphere's own bounding
BOX (not just the sphere itself -- an axis-aligned box around a sphere is
~1.9x the sphere's own volume), every subsequent Stage A step
(`SmoothingRecursiveGaussian`, thresholding, `ndimage.label` connected
components, `_remove_boundary_spike`'s opening, then
`postprocess.run_full_postprocess`'s speck removal/hole filling/boundary
smoothing, then `mesh_export.label_map_to_mesh`'s marching-cubes
extraction) still runs a full pass over that box -- at this scan's
spacing, on the order of 100M+ voxels, for EVERY one of those ~9 steps.
Each step in isolation might be individually reasonable; the total could
easily add up to what Thomas is seeing.

**Do NOT guess at which one is slow again -- the data now exists.**
Timing instrumentation was added this session (2026-07-29, temporary,
marked for removal once the bottleneck is found) printing `[pinna
timing] <stage>: X.Xs` to the Slicer Python console for every stage:
coarse crop, `build_spherical_roi_mask`, crop-to-bounding-box, then
inside `segment_pinna_region` (Gaussian smoothing, array conversion,
thresholding, connected-component labeling, component selection, spike
removal), then inside `postprocess.run_full_postprocess` (remove_small_specks,
fill_holes, close_small_tunnels if used, smooth_boundary), then
push-to-Slicer/mesh-export/load steps in `page_pinna_review.py`. This
session's own EDT-opening dead end (see above) is a direct lesson in why:
the "obviously right" guess (ball-SE kernel-volume scaling) was measured
and found backwards. **Next session: ask Thomas to run segmentation once
more on this same slow scan and paste the full `[pinna timing]` block**
before proposing any fix -- that will show exactly which of the ~9 steps
actually dominates, rather than guessing again.

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

**Implemented, passed synthetic validation, NOT yet real-Slicer tested:**

- **Hessian-eigenvalue sheet/plate enhancement** (`core/sheetness.py`,
  gated behind `config.ENABLE_SHEET_ENHANCEMENT` = `False` by default).
  Directly attacks both remaining complaints via local SHAPE, not just
  intensity: `bright_sheetness()` boosts recall for thin bone-wall regions
  that partial-volume below `bone_threshold`; `dark_sheetness()` vetoes a
  bone classification wherever the local geometry looks like a thin
  low-intensity septum (the scutum/malleus gap), even where blurred
  intensity crept above threshold. A pipeline-level synthetic test (a full
  synthetic canal volume with a solid "normal" wall control region, a
  deliberately 1-voxel-thin wall region, a 1-voxel axial gap slot cut
  through an otherwise-solid wall, and an isolated noisy-tissue stress
  region, all run through the real `segment_bone_wall()`) found: thin-wall
  recall 84.6%→94.9%; the gap went from 98% falsely bridged to a full,
  clean topological separation (0% false-bone in the gap slot, 2 connected
  components instead of 1); normal-wall recall barely moved
  (97.9%→96.8%); and — critically — zero extra false positives in the
  noise-stress region (3.4% in both configs) once gamma was properly
  calibrated (see below). Runtime: ~7x slower than plain thresholding on a
  100³-voxel test volume (~7s vs ~1s) — noticeably more than
  CurvatureFlow's own cost, but still plausibly fine for an explicit "Run
  Segmentation" click; restricting the Hessian computation to a padded
  box around `shell_array` (documented in config.py) is the first
  optimization to try if real use finds this too slow.

  Two real surprises from calibration, both documented in config.py:
  1. **Sheetness runs on the RAW pre-CurvatureFlow intensity, not the
     smoothed field** — despite `segment_bone_wall()`'s own intensity
     threshold decision correctly still using the CurvatureFlow-smoothed
     field, unchanged. The dark-sheetness gap veto never achieved true
     topological separation on the smoothed field at any threshold tried;
     only the raw field's sharper local Hessian signature let it fully
     succeed.
  2. **`SHEETNESS_NOISE_SUPPRESSION_GAMMA` turned out to depend heavily on
     noise amplitude AND on how sharp the synthetic test construction is**
     — not something either synthetic test alone could have predicted. A
     first, isolated calibration (gamma=2.0, a noisy cube far from any
     threshold) looked airtight there but catastrophically failed once
     re-tested at the actual decision boundary (noise baseline exactly
     `bone_threshold`, a more realistic amplitude): 93% false positives,
     vs. the eventual gamma=18's 3.4% (matching the no-sheetness baseline
     exactly). A THIRD instance of this section's synthetic-tests lesson
     below — worth remembering the next time any of these constants get
     retuned: always re-check a noise stress test sitting exactly at the
     real decision threshold, not just an easy, far-from-threshold one.

  **First real-Slicer A/B (2026-07-29): came out WORSE than plain
  thresholding** — large areas of soft tissue misclassified as bone, thin
  walls still struggling. Root cause: `bright_sheetness`'s OR-boost had NO
  floor on absolute intensity -- it promoted any voxel with a strong
  enough local sheet-like SHAPE regardless of how far its actual HU value
  was from anything bone-plausible. Real soft tissue is full of genuine
  thin, locally-flat anatomical interfaces (fascia, muscle septa, vessel
  walls, organ capsules) that are geometrically indistinguishable from a
  partial-volumed bone wall in pure Hessian-eigenvalue terms -- no amount
  of `SHEETNESS_NOISE_SUPPRESSION_GAMMA` retuning can fix this, since it's
  not a noise problem, it's that shape alone genuinely cannot tell "thin
  bone" from "thin fascia" without also checking intensity. **Important
  caveat**: several synthetic attempts to reproduce this exact failure
  (noisy tissue sitting at the decision boundary, periodic low-contrast
  fascia septa, a clean sharp tissue-tissue step, a noisy version of that
  same step) all stayed at 0% false positive even under the
  then-current gamma=18 -- meaning the fix below is a principled,
  first-principles response to the reported symptom category, NOT a
  confirmed reproduction of the real mechanism. Real CT data evidently has
  some characteristic none of these synthetic models captured.

  **Fix**: `BRIGHT_SHEETNESS_INTENSITY_MARGIN_HU`/
  `DARK_SHEETNESS_INTENSITY_MARGIN_HU` (config.py, both 150.0 to start) --
  a voxel can only be OR-boosted into bone if its own intensity is already
  within the bright margin of `bone_threshold` (plausibly a partial-
  volumed wall, not generic soft tissue), and only AND-vetoed out of bone
  if it's within the dark margin (plausibly a blurred air/soft-tissue gap,
  not unrelated dense bone). A hard, categorical guarantee (unlike gamma,
  which is statistical) -- any voxel further than the margin from
  `bone_threshold` can never be touched by sheetness, regardless of shape
  response.

  **Caught a real bug in this fix before it shipped, via the same
  synthetic gap test**: the first version gated BOTH the bright boost and
  the dark veto on `smoothed_array`'s proximity to `bone_threshold`. For
  the dark veto this is backwards and silently re-broke the gap-
  separation fix entirely (verified: gap false-bone fraction jumped from
  0% back to 94%) -- the whole reason the veto is needed is that
  CurvatureFlow blurs a real gap's *smoothed* intensity up toward (and
  past) `bone_threshold`, so gating eligibility on that same smoothed
  value excludes exactly the case the veto exists to catch. Fixed by
  gating the dark veto's eligibility on `raw_array` (the true, unblurred
  value) instead -- the bright boost's gate correctly stayed on
  `smoothed_array`, since a genuinely thin wall's smoothed intensity
  legitimately dips just under threshold, unlike a blurred-up gap's.

  **Re-validated after the fix**: thin-wall recall 96.2% (up from the
  no-sheetness baseline's 82.2% in this test run), gap veto still achieves
  full topological separation (0% false-bone, 2 connected components),
  normal-wall recall unchanged (97.5%), and all four stress regions (noisy
  tissue at threshold, fascia septa, deep soft tissue, sharp tissue-tissue
  edge) stayed at their no-sheetness baseline -- zero new false positives
  introduced by sheetness in any of them, now with an added categorical
  safety net the earlier version lacked.

  **Second real-Slicer A/B (still 2026-07-29): soft tissue fixed, but back
  to ~old-segmentation results** -- thin walls still missing, gap still
  bridged. Thomas supplied real numbers this time (seed-calibrated
  `bone_threshold=760`/`air_threshold=-441`; solid bone reads 1000-1400,
  peaking ~1300 at the missing-wall location; the bridged gap reads ~-1100;
  the missing wall is ~2-3 native voxels wide; native slice spacing is
  0.5mm on this scan, "as low as 0.25mm" on others), which led to a much
  bigger finding than a simple constant retune:

  **`page_dicom_load.py` deliberately does NOT resample to
  `TARGET_VOXEL_SPACING_MM` (0.3mm)** -- confirmed by reading that file's
  own docstring: the live pipeline runs on each scan's native voxel
  spacing, which varies scan to scan. This whole sheetness feature (scales,
  gamma, everything) had been implicitly calibrated assuming a uniform
  0.3mm working resolution that was never actually true on real data.
  Concretely: a Gaussian-derivative filter's noise sensitivity depends on
  how many voxels its kernel actually spans (`sigma_voxels`), not the
  absolute mm scale -- the same fixed absolute-mm scale set that gamma=18
  handled fine at an assumed 0.3mm spacing needed gamma>=100 to behave the
  same way once actually tested at this scan's real 0.5mm spacing, with
  zero warning that the earlier calibration had silently stopped applying.

  **Fixes**:
  1. `SHEET_ENHANCEMENT_SCALES_MM` replaced with
     `SHEET_ENHANCEMENT_SCALE_MULTIPLIERS` (config.py) -- scales are now
     computed per-call as multiples of the scan's own native spacing
     (`segment_threshold.segment_bone_wall()`), not a fixed mm list, so
     `sigma_voxels` stays consistent regardless of a scan's actual (and
     possibly quite different) native resolution. `core/sheetness.py`'s
     `bright_sheetness()`/`dark_sheetness()` no longer default `scales_mm`
     from config at all -- it's scan-dependent, so the caller always
     computes and passes it explicitly now.
  2. `SHEETNESS_NOISE_SUPPRESSION_GAMMA` raised 18→100, re-validated via a
     multiplier×gamma grid search against Thomas's real HU numbers at
     0.5mm spacing.
  3. `BRIGHT_SHEETNESS_INTENSITY_MARGIN_HU` widened 150→300, since real
     `bone_threshold` (760) and cortical bone (1000-1400) sit at a much
     higher absolute scale than this was first picked against (100/800).

  **Re-validated** (synthetic volume rebuilt to match Thomas's real
  numbers -- 0.5mm spacing, bone_threshold=760, bone~1300, gap~-1100):
  thin-wall recall 96.0%→98.0%, zero regression on normal-wall recall or
  any of the four false-positive stress regions. **Honest limitation**:
  at this specific real HU scale, the synthetic gap-bridging test no
  longer reproduces a dropout even without sheetness (the larger absolute
  contrast in this test apparently keeps CurvatureFlow from pushing the
  gap's blurred intensity over threshold in this particular synthetic
  construction) -- so this round did NOT specifically re-validate a fix
  for the gap-bridging complaint the way the previous round did. Whether
  the gap bridging is actually fixed now is unknown until Thomas re-tests.

  **Third real-Slicer A/B (still 2026-07-29): no improvement** -- thin
  walls still missing, scutum/malleus still bridged, essentially back to
  pre-sheetness results. Three rounds of real-scan feedback have now each
  caught a different calibration/assumption problem (spacing, HU scale,
  and now apparently something the HU-scale recalibration still didn't
  reach) without synthetic testing predicting any of them in advance --
  at this point further blind constant-tuning on this specific approach
  looks like diminishing returns. Thomas asked for literature/tooling
  research into alternatives instead of another guess -- see the
  "Ear-canal/temporal-bone segmentation research (2026-07-29)" section
  below for the full findings (atlas/shape-model approaches, existing
  Slicer extensions, MONAI Label). Thomas chose to try the cheapest lead
  first: self-calibrating the noise-gate constant, below.

  **Self-calibrating noise gate implemented (2026-07-29)**, replacing the
  fixed `SHEETNESS_NOISE_SUPPRESSION_GAMMA` entirely. Adapted from the
  published Krcah et al. bone-sheetness filter (femur segmentation, 0.98
  Dice, explicitly designed for "invariance to density calibration" --
  https://github.com/ypauchard/ITK-KrcahSheetnessImageFilter): instead of
  a fixed absolute number, `core/sheetness.py` now computes the noise-gate
  value fresh at call time, per scale, as `SHEETNESS_GAMMA_AUTO_SCALE *
  median(S)`, where S is measured over `calibration_mask`
  (`segment_threshold.py` passes its `shell_array` -- the region the
  decision is actually made in, not the whole crop). Two things had to be
  found by direct testing, not assumed: MEDIAN not mean (a first version
  used mean(S) over the whole cropped ROI and it barely gated anything --
  the mean gets dragged toward whatever fraction of the crop is empty
  background), and restricted to the shell, not the whole array (same
  reason).
  
  **Validated for actual cross-scale portability** -- not just
  recalibrated at one scale like the previous three rounds -- by running
  the identical `SHEETNESS_GAMMA_AUTO_SCALE=2.0` against BOTH the original
  0.3mm/bone_threshold=100 synthetic reconstruction AND the 0.5mm/
  bone_threshold=760 one built from Thomas's real numbers, with no
  retuning between them:
  - 0.5mm/760 scan: thin-wall recall 96.0%→99.75%, full gap-veto
    separation (2 components), zero new false positives in any of 4
    stress regions.
  - 0.3mm/100 scan (the original worst-case 1-voxel gap): thin-wall
    recall 82.2%→96.2%, gap false-bone fraction 98.2%→0.25% (not fully
    separated -- stayed at 1 connected component instead of 2, a real but
    imperfect improvement), zero new false positives in 3 of 4 stress
    regions (the 4th, a hard tissue-tissue edge, showed ~30-32% false
    positive in BOTH configs -- a pre-existing baseline artifact of that
    specific synthetic construction at 0.3mm, not something sheetness
    introduced).
  
  This is the first time in this feature's history that ONE set of
  constants has shown a real improvement at two meaningfully different
  scan scales without per-scale retuning -- categorically different
  evidence than the previous three rounds, though still synthetic-only.

  **FOURTH real-Slicer A/B (still 2026-07-29): still no noticeable
  improvement.** Thomas then directly asked whether the code changes were
  even being applied -- a fair question after four rounds of "should
  help" landing as "looks the same." Two things were checked/added in
  response, and this is exactly where the NEXT session should pick up:
  1. **Confirmed NOT a stale-file/wrong-path problem**: Thomas ran
     `import config; print(config.__file__)` in Slicer's Python console
     and confirmed it points to the actual file being edited
     (`E:\EarReconstructionPlanner\EarReconstructionPlannerLib\config.py`),
     with `ENABLE_SHEET_ENHANCEMENT`/`SHEETNESS_GAMMA_AUTO_SCALE` reading
     correctly. Slicer IS loading the intended code.
  2. **Diagnostic logging added, NOT YET CHECKED** -- this is the
     immediate next step, not a new theory to test. `core/
     segment_threshold.py`'s sheet-enhancement block now logs (visible in
     Slicer's Python console, every "Run Segmentation" click):
     `shell_voxel_count`, the scales/gamma_scale/thresholds in use, and
     the actual computed bright/dark response max + fraction exceeding
     threshold. `page_scutum_review.py`'s status label also now shows
     `[Sheet enhancement: ON (gamma_scale=2.0)]` directly in the UI. If
     `shell_voxel_count` is ever 0, that's a concrete bug (median of an
     empty array is `nan`, silently making every bright/dark comparison
     `False` -- sheetness would do NOTHING while looking like it's
     running). **Nobody has looked at this log output yet** -- next
     session should ask Thomas to run segmentation once more and report
     what that log line actually says before doing anything else. If the
     numbers look sane (non-zero shell, plausible response fractions) and
     it still doesn't help, that's real evidence the filter genuinely
     isn't fixing this on real data, not a bug -- and the honest
     conclusion at that point would be to stop tuning this approach and
     move to one of the larger-effort alternatives below (atlas/shape-
     model, or trying the existing ABL Slicer extension).

  `ENABLE_SHEET_ENHANCEMENT` is currently `True` (self-calibrating
  version). Flip to `False` to go back to plain thresholding if needed.

## Ear-canal/temporal-bone segmentation research (2026-07-29)

Prompted by the sheetness feature above not improving results on a third
real-scan attempt -- Thomas asked for literature/tooling research into
alternative approaches rather than continued blind parameter tuning.
Full findings/sources in that session's conversation; summary here for
future reference.

**Validating context**: even funded academic groups with dedicated manual
training data hit the same wall this project has. Multiple published deep-
learning temporal bone segmentation papers report their WORST results on
the thinnest/smallest structures (stapes Dice as low as 0.56-0.70 vs.
0.85-0.95+ for larger structures like the labyrinth), and explicitly state
some structures (basilar/vestibular membranes) are "too thin for precise
identification" at conventional clinical CT resolution. This is a genuine,
widely-reported resolution-limit problem, not a sign this project's
specific approach is uniquely flawed.

**Most actionable near-term lead**: the published Krcah et al. bone-
sheetness filter (used for femur segmentation, 0.98 Dice, "invariance to
density calibration" explicitly cited as a design goal -- see
[ITK-KrcahSheetnessImageFilter](https://github.com/ypauchard/ITK-KrcahSheetnessImageFilter))
computes its noise-suppression constant AUTOMATICALLY per scan -- e.g.
as the mean Hessian trace (or a fixed fraction of the max Frobenius norm,
in their `AutomaticSheetnessParameterEstimationImageFilter`) over the
image actually being processed, rather than a fixed hand-picked value.
This directly targets this project's repeated failure mode: `core/
sheetness.py`'s `SHEETNESS_NOISE_SUPPRESSION_GAMMA` has now needed
re-guessing every time scan characteristics changed (spacing, HU scale)
with no warning it had silently stopped applying. Krcah's filter also
applies an unsharp-masking pre-sharpening step (`I + k*(I - Gaussian(I))`)
before the Hessian, which might help thin-structure recall independent of
gamma. Worth trying before anything more drastic.

**A structurally different (and probably more promising) paradigm**:
atlas-based / active-shape-model segmentation, as used by Vanderbilt's
Noble/Dawant group for temporal bone structures (Dice>0.8 for cochlea/
malleus/incus/SCCs -- see
[Atlas-Based Segmentation of Temporal Bone Anatomy](https://pubmed.ncbi.nlm.nih.gov/28852952/)).
Instead of trying to see a structure directly in the patient's own
(resolution-limited) intensity data, this registers a pre-built shape
model -- in Noble et al.'s case, built from HIGHER-RESOLUTION micro-CT --
onto the patient's lower-resolution clinical CT, so the final surface
comes from "where this structure almost always is, relative to
everything else visible" rather than "what can be directly seen." This is
the one approach found that doesn't fight the same resolution ceiling
this project's threshold/sheetness work has hit repeatedly, since it
imports information from a genuinely higher-resolution reference instead
of trying to extract more from the low-res scan itself. The
[OpenEar library](https://www.nature.com/articles/sdata2018297) is a
public temporal bone dataset (real CT + micro-CT ground truth) that could
serve as that higher-resolution reference without needing to bank 15
of Thomas's own cases first (c.f. `MIN_CASES_TO_TRAIN` for the existing
Stage B nnU-Net plan). Substantially bigger effort than a filter tweak --
registration pipeline, shape model construction/fitting -- not a quick win.

**Existing off-the-shelf tools worth trying directly, at low cost**:
- [Slicer-ABLTemporalBoneSegmentation](https://github.com/Auditory-Biophysics-Lab/Slicer-ABLTemporalBoneSegmentation)
  (Western University's Auditory Biophysics Lab) -- an installable 3D
  Slicer extension with a pretrained temporal bone segmentation network;
  no training data needed, though it needs a local Docker instance or
  their inference server. Worth trying as an independent second opinion
  on the same problem scan before building anything new.
- MONAI Label + 3D Slicer integration -- interactive AI-assisted
  segmentation (DeepEdit/DeepGrow: click points, model refines) plus
  Auto3DSeg for bootstrapping a model without much data. No temporal-
  bone-specific pretrained bundle found in the MONAI Model Zoo, but the
  interactive click-to-refine workflow could be a genuinely different,
  more surgeon-friendly correction path than hand-tuning thresholds/
  sheetness constants blindly.

**Dead ends confirmed again this session**: continued sheetness constant
retuning (gamma, scale multipliers, intensity margins) across 3 rounds of
real-scan feedback -- each round fixed the thing it was tuned against
without fixing the reported symptom, and the underlying resolution-limit
problem doesn't appear to be something this class of fix (shape-only
analysis on the same low-resolution intensity data) can fully solve. Not
formally reverted (still behind `ENABLE_SHEET_ENHANCEMENT`, off by
default would be the safe posture if this is abandoned) but not worth
further blind tuning without trying one of the above first.

**Atlas-based approach follow-up (2026-07-29, later same day) — IN
PROGRESS, blocked on Thomas talking to his supervisor, pick up here:**

- **ABL Slicer extension ruled out**, specifically on surgeon-usability
  grounds, not technical merit: it requires either a local Docker
  instance or Thomas's remote ABLInfer server for the actual DL
  inference step (the registration part alone, via SlicerElastix, is
  fine — CPU, bundled binary, no Docker). Blockers: 6GB VRAM floor is
  right at Thomas's GTX 1060's ceiling, not comfortably above it; ~26GB
  RAM/swap recommended; Docker GPU passthrough on Windows needs
  WSL2 + NVIDIA container toolkit, real setup burden for a surgeon end
  user; the remote server option means sending patient CT to an external
  Western University server, a real PHI concern for actual patient scans.
  Confirmed the extension itself is real and installable (BSD-3-Clause,
  github.com/Auditory-Biophysics-Lab/Slicer-ABLTemporalBoneSegmentation)
  — rejected for the deployment constraints, not because it doesn't work.
- **Chosen direction instead: multi-atlas registration + label fusion**,
  entirely within the existing CPU/no-Docker/no-GPU stack. Mechanism:
  register several pre-built "atlas" scans (with known-good labels) onto
  the patient's scan via SimpleITK's registration (already a dependency),
  warp each atlas's label along, then fuse the several proposals with
  SimpleITK's built-in `sitk.STAPLE()`/`sitk.LabelVoting()` (confirmed to
  exist, no new dependency) into one consensus mask. Intended role: a
  **soft prior in ambiguous threshold voxels** (same role sheetness was
  trying to play), not a hard replacement of the existing threshold's
  output — a patient's own live segmentation should still win wherever
  it's confident, so real patient-specific anatomy doesn't get regressed
  toward "what the atlas set looks like." Natural integration point:
  `segment_dl.py`'s existing Stage B stub (currently always falls back to
  Stage A) — this would become the real Stage B implementation.
- **Atlas source: OpenEar** (Zenodo record 1473724, CC-BY 4.0, 8 adult
  cadaveric specimens, CBCT + micro-CT, ~59GB total). Confirmed it labels
  malleus/incus/stapes/facial nerve/inner-ear compartments/nerves/vessels
  but has **no "canal wall"/"scutum" label** — that's not an oversight,
  it's because the canal wall isn't a discrete anatomical object the way
  a free bone is (no natural boundary, just an arbitrary thickness
  cutoff, same reason your own `BONE_WALL_THICKNESS_MM` is a chosen
  constant not a measured one). Plan: Thomas manually segments the wall
  boundary he wants directly on OpenEar's micro-CT (ex-vivo, much higher
  bone contrast than any live patient scan, so a plain threshold +
  Segment Editor cleanup should get most of the way there) — creating the
  atlas label that doesn't exist in the source data. Bonus this unlocks:
  **leave-one-out cross-validation** (build atlas from 7 specimens, test
  against the 8th's known manual truth) — a real quantitative check
  against ground truth, stronger evidence than any synthetic NumPy test
  in this project's history, all of which have at some point failed to
  predict real-scan behavior (see [[feedback_synthetic_tests_limits]]).
- **Blocker found: OpenEar is adult-only, and pediatric temporal bone
  anatomy differs qualitatively, not just by scale** — directly relevant
  since Thomas's actual patient population skews pediatric. Verified via
  literature, not assumed: the EAC doesn't reach adult size/shape until
  ~age 9 (neonatal canal is nearly straight, not yet curved); the
  tympanic ring isn't fully fused inferiorly at birth, and part of the
  canal floor (the "lamina fibrosa") stays unossified **cartilage**,
  not bone, until roughly age 3-4 (incomplete fusion here is the origin
  of the foramen of Huschke, a normal persistent bony gap — not a defect
  to "correct"); ossicles have their own separate postnatal ossification
  timeline, so even the narrower malleus-only-veto version of this idea
  isn't automatically age-safe either. This means an adult atlas risks
  imposing a "solid bone" hypothesis onto a region that is genuinely
  cartilage in a young child — a qualitative tissue-type mismatch that no
  amount of registration (rigid, affine, or deformable) can correct,
  categorically different from every resolution/calibration problem this
  project has hit so far.
- **Searched for a pediatric-specific equivalent — dead end, confirmed by
  directly inspecting real data, not just reading docs/abstracts**
  (consistent with [[feedback_verify_before_trusting_apis]]):
  - Seattle Children's "Temporal Bone Atlas" is an educational image
    viewer, not downloadable segmented volumes (its own page 404'd on
    direct fetch).
  - Ke et al. (QIMS 2023, PMC10006112) + its GitHub release
    (`Dnkii/Automatic-Segmentation-of-Temporal-Bone-Structures-from-
    Clinical-Conventional-CT`, GPL-3.0) claims 40 pediatric + 40 adult
    cases, 11 structures including EAC (pediatric Dice 0.831 vs adult
    0.859, P=0.005) — but directly loading the actual released
    `mask.nii.gz` files and checking unique voxel values found only
    `{0,1,2,3}`: background + cochlear labyrinth/ossicular chain/facial
    nerve. **EAC is not in the public release** despite the paper's
    claims, and only 30 of the paper's 80 cases are even present.
    Volumes are also tiny crops (64x64x80 voxels, ~27x27x32mm FOV)
    centered on the inner ear — likely insufficient canal coverage even
    if EAC had been included. Pediatric age range is never disclosed
    anywhere in the paper ("children," no bounds given) — a real problem
    given the ossification-timeline finding above. An unverified external
    link in the README (a personal Beihang University file-share) might
    hold the fuller 80-case set, but it's non-permanent hosting and
    wouldn't resolve the missing age-range gap regardless.
  - **Lesson**: a paper's claimed dataset contents and what's actually
    publicly released can diverge significantly — this was only caught
    by loading the real files and checking real label values, not by
    reading the README or abstract, which both implied more than what's
    actually there.
- **Open decision — NOT YET MADE, Thomas needs to talk to his supervisor
  first, do not assume a direction next session**: three options on the
  table —
  1. Use Thomas's own already-consented pediatric clinical cases as
     private local atlas data (manually segmented, kept local, never
     committed to the open-source repo). Solves the confidentiality
     concern directly (it's about open-sourcing patient data, not using
     it locally) and sidesteps the public-data search entirely, but only
     makes the tool work well for Thomas's own institution out of the
     box, not other institutions' surgeons.
  2. Keep searching for/negotiating access to public pediatric data (try
     the Beihang link; email Ke et al.'s authors for the full 80-case set
     with EAC, and to ask about age range).
  3. Fall back to adult-only OpenEar, but explicitly gate/disable the
     atlas correction below some age threshold where the cartilage-vs-
     bone mismatch is most likely, treating it as adult/near-adult
     anatomy only otherwise.

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

**Still open**: the scutum/malleus fusion persisted even with CurvatureFlow
smoothing alone (a direct synthetic test found neither Gaussian nor
CurvatureFlow preserves a true gap at exactly 1 voxel =
`TARGET_VOXEL_SPACING_MM`). The Hessian sheet/plate enhancement above was
built specifically to address this and passed synthetic validation
cleanly — but per this section's own established practice, it isn't
"still open" vs. "fixed" until a real-scan A/B actually confirms it one
way or the other.

**Lesson**: synthetic tests here validated real mechanisms (calibration
math, tunnel-reopening guard, noise-vs-gap-preservation tradeoffs, and
now the sheetness eigenvalue-ratio discriminant itself) but THREE times
now failed to predict real-scan-relevant outcomes on the first try, in
different ways each time — epitympanum inclusion looked clean
synthetically but damaged real walls; CurvatureFlow's real fix was never
fully reproduced synthetically either (a flat-slab and a curved-tube test
both showed zero measurable erosion risk, so the synthetic evidence for
adopting it was suggestive, not proof); and the sheetness noise-
suppression constant's first calibration (an isolated, easy noise test)
looked airtight but catastrophically failed once re-tested at the actual
decision boundary with a harsher, more realistic noise amplitude — caught
this time before shipping, by testing harder rather than trusting the
first green synthetic result. Real-scan A/B comparison by Thomas was what
actually decided the first two cases, and remains the outstanding step for
the third. Doesn't invalidate synthetic-first testing (it still caught a
real bug in sub-voxel meshing before it ever reached Slicer, and this time
caught its own calibration gap before shipping too) — just confirms the
existing Testing Approach note below: synthetic tests catch what they
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
