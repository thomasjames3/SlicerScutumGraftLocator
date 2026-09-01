# CLAUDE.md

Condensed working context: root cause + fix + current status per item, not
the blow-by-blow debugging trail (older, more verbose versions of this file
are in git history if ever needed).

---

## Project background

Thomas: medical researcher/developer building surgical planning tools for
**microtia reconstruction** (scutum grafting using pinna cartilage).
Comfortable with 3D Slicer/Blender/SimpleITK, newer to Python. Windows. Two
merged sub-projects:

1. **Curvature Project v4** — standalone tool (own venv) that compares a
   scutum defect mesh to a pinna mesh and heatmaps the best cartilage
   harvest site. Kept as a reference-only copy; its algorithm has been
   **ported in-process** into this extension (`core/curvature/`).
2. **Ear Reconstruction Planner** (this repo) — a Slicer extension that
   generates the scutum defect mesh and pinna mesh from a CT scan, then
   runs the curvature comparison in-process.

**Priority above all else:** zero-Python-experience surgeon usability —
sliders not raw thresholds, drawing on 3D surfaces not typed coordinates,
one-time dependency install, plain-language instructions.

---

## Current status — pick up here

**Active thread: Scutum finalize mesh quality, round 5** — round 4's first
real-Slicer test (2026-08-31) hit a bug: "Preview 3D Result" completed
normally but the 3D view showed nothing. Root cause CONFIRMED via
diagnostics (not just fixed-and-hoped): the whole-volume, no-shell-
restriction threshold this page uses, combined with zero postprocess/
fill_holes cleanup before component selection, produced **1054 separate
mesh components** inside the ROI crop on that scan — one real one
(2416.34mm²) and 1053 debris fragments down to near-zero area.
`select_mesh_component_nearest_axis()` had no size weighting, so it could
land on any of the 1053 specks instead of the real wall. Fix: added a
`min_area_mm2` floor (`config.SCUTUM_MESH_COMPONENT_MIN_AREA_MM2` = 5.0mm²)
that excludes debris from the axis-distance comparison before picking —
confirmed on a re-run of the SAME scan to correctly select the 2416.34mm²
component (2.75mm from axis) and render correctly, matching the live
Segment Editor view. Diagnostic `[mesh_export diag]` prints (mesh size
through export → ROI crop → component selection) were left in place for
now, same TEMPORARY category as the anisotropy-fix diagnostics.

**Status: confirmed on 1 real scan, both halves of round 4's original
checklist now pass on that scan** — (1) finalized mesh matches the live
Segment Editor view, confirmed above; (2) Isolate Patch on the scutum draw
page against this mesh, confirmed working perfectly, despite this round's
finalize path skipping tunnel-closing postprocess entirely (deliberate
experiment) — no `LoopDoesNotSeparateError`, no fallback needed. Same
"needs more scans before closing" caveat as every other real-scan fix in
this project applies to BOTH the component-selection fix and the no-
postprocess experiment — the 5.0mm² threshold is an untuned guess, the
debris-component-count (1054) is scan/threshold-dependent, and one clean
Isolate Patch pass doesn't rule out a genus>0 topological handle on a
different scan's anatomy. Full history under "Scutum finalize mesh
quality" below.

**Pinna anisotropy tear**: fix shipped 2026-08-25, confirmed "much better"
on the one real scan that showed it. Provisional — Thomas wants more scans
tested before closing this out. Until then leave the TEMPORARY diagnostic
prints (`[mesh_export diag]`, `[pinna diag]`) and the forced-visible
`restartSlicerButton` (`page_setup.py`) in place. Full history under "Pinna
mesh anisotropy tear" below.

**Pinna skin-threshold calibration "inaccuracy" — RESOLVED (not a bug,
Thomas's explicit call)**: reported gap between the calibrated threshold
(~-470 HU) and a hand-tuned "accurate" value (~-700 HU) had two causes: (1)
a real mesh-fragmentation bug, fixed 2026-08-26 (see next item); (2) after
that fix, remaining holes at the cymba and helix rim are a genuine
resolution limit, not a bug — those are thin, folded structures, and this
scan's native 2.5mm Z-spacing partial-volume-averages their true intensity
below the air/tissue midpoint even though it's real tissue. Both
calibration seed samples were confirmed plausible, so the midpoint formula
is correctly finding the *physical* boundary — that boundary just isn't
permissive enough for thin folds. Same category as `wall_quality.py`'s
thin-wall correlation for the bone case: a real resolution limit, not
something threshold-tuning escapes. Thomas explicitly declined biasing the
formula toward air to pre-compensate (not worth the generalization risk
given the slider is already a one-drag manual fix). **Formula stays the
plain midpoint — don't revisit unless Thomas raises it again.**

**Pinna mesh: disconnected component after marching_cubes — fixed AND
CONFIRMED on a real scan 2026-08-26**: found while investigating the
threshold report above. Root cause: `mesh_export._verts_faces_to_trimesh()`
built a `trimesh.Trimesh` straight from `marching_cubes()`'s output with no
connectivity check. On a real scan this produced a 3-way-split mesh (one
main blob + one 119.77mm² separate watertight chunk + one degenerate
sliver) even though every voxel-space diagnostic upstream confirmed exactly
1 connected component — most likely a razor-thin single-voxel bridge that
marching_cubes meshed correctly but that trimesh's own vertex-merge/
cleanup then severed. `fill_holes()` can't fix this (it only patches
boundary loops within an already-connected piece). The existing fragment-
dropping logic (`_repair_decimation_fragments()`) only runs from the
decimation branch and is size-tuned for tiny slivers, so it never caught
this. **Fix**: `_verts_faces_to_trimesh()` now splits into connected
components and keeps only the largest by area, unconditionally, before
`fill_holes()`/smoothing — safe because the feeding voxel mask is already
proven single-component, so any split found here is provably a meshing
artifact. Also added `allow_degenerate=False` to both `marching_cubes()`
call sites to stop zero-area triangle debris at the source. Confirmed on
the real scan (console: 3 components collapsed to 1,
`decimate_to_target_resolution()` confirmed `components=1` after). Not yet
spot-checked against the scutum bone-wall path (`label_map_to_mesh_
subvoxel`, same underlying function) — low risk, worth keeping in mind if
a scutum mesh ever looks unexpectedly different after this change.

**End-to-end confirmation status**: DICOM load → scutum landmarks → pinna
landmarks/review/draw → scutum review/draw confirmed working 2026-07-28/29,
but pinna review/draw and scutum review were substantially rewritten after
that (decimation, component-selection fix, tight-bbox crop, background-
thread+progress-bar wiring, Segment Editor rework). Not re-confirmed
end-to-end since. Verify page and Curvature page's Qt widgets (progress
streaming, results table, heatmap display) have never been runtime tested
at all; the curvature algorithm itself is validated only against synthetic
meshes.

**Calibration (2026-07-31, RESOLVED)**: 3 clicks total — pinna review page:
air + soft-tissue points (midpoint → skin threshold); scutum review page:
bone point (midpoint with the pinna soft-tissue point → bone threshold).
Both pinna points required before proceeding. Confirmed directionally
correct on a real scan (predicted -465 HU vs. hand-tuned -550) — Thomas
deferred closing that ~85 HU gap until more scans are tested; **don't add a
correction constant unprompted**.

**Open bug, never debugged**: "Reset All Points" on the scutum landmarks
page. See `_on_reset_clicked` in `page_scutum_landmarks.py`.

**Probably fixed, unconfirmed**: Slicer crash opening the module before a
DICOM volume is loaded — two speculative fixes applied, hasn't recurred
across many later sessions.

---

## Architecture

Scripted module (no CMake/compiled build), `QStackedWidget` wizard, one
plain-language step at a time:

```
0. Setup   0.5. Welcome (tutorial-mode only)
1. DICOM load
2. Scutum landmarks (2-point axis)
3. Pinna landmarks (1-point + side)
4. Pinna review   5. Pinna draw
6. Scutum review   7. Scutum draw
8. Verify   9. Curvature
10. Complete (download step, final page)
```

**Pinna-first reorder**: pinna segmentation/draw runs before scutum
review/draw so the surgeon can iterate on scutum shape without re-touching
pinna state (`clear_downstream_state` keyed off `PAGE_ORDER` position).
Scutum *landmarks* (page 2) stayed early only because
`mesh_isolate.crop_toward_canal()` needs its axis direction before pinna
draw runs, and it's cheap (2 clicks, no segmentation). Each stage hides-on-
entry/re-shows-on-return the other stage's leftover models — not yet
runtime-tested.

**`.ui` files + separate controllers**: layout lives in
`Resources/UI/page_*.ui` (Qt Designer editable without touching Python);
`EarReconstructionPlannerLib/pages/page_*.py` wires logic by widget name.

---

## Directory structure

```
EarReconstructionPlanner/
├── EarReconstructionPlanner.py       # module entry
├── EarReconstructionPlannerLib/
│   ├── config.py                     # ALL tunables
│   ├── dependencies.py               # pip_install for Setup page
│   ├── wizard_state.py               # WizardState dataclass + PAGE_ORDER
│   ├── curvature_integration.py      # in-process bridge to core/curvature/
│   ├── core/                         # Slicer-independent, unit-testable
│   │   ├── io_utils.py               # DICOM/volume load; flip_ras_lps(); resample_to_bounded_anisotropy()
│   │   ├── landmarks.py              # EarCanalLandmarks (2pt)
│   │   ├── pinna_landmarks.py        # PinnaLandmarks (1pt + side)
│   │   ├── roi_crop.py               # ROI mask; MANDATORY coarse-crop-first
│   │   ├── segment_threshold.py      # Stage A bone-wall (ear canal)
│   │   ├── segment_pinna_threshold.py # Stage A skin-surface (pinna)
│   │   ├── segment_dl.py             # Stage B hook, unimplemented, falls back to Stage A
│   │   ├── postprocess.py            # speck removal, hole fill, smoothing, close_small_tunnels()
│   │   ├── mesh_export.py            # label map -> trimesh; RAS<->LPS flip; fill_holes()
│   │   ├── mesh_isolate.py           # drawn-loop -> isolated patch; crop_toward_canal()
│   │   └── curvature/                # in-process port of Curvature Project v4
│   └── pages/                        # page_setup.py ... page_curvature.py, base_page.py
└── Resources/UI/                     # EarReconstructionPlanner.ui + page_*.ui
```

---

## `core/` reference

- **`config.py`** — every tunable constant.
- **`landmarks.py`** — `EarCanalLandmarks`: `canal_opening` (bony opening,
  not skin level), `near_eardrum`.
- **`roi_crop.py`** — truncated-cylinder ROI from the 2 landmarks. **Must
  coarse-crop before `build_roi_mask`/`build_spherical_roi_mask`** — both
  evaluate every voxel; skipping OOMs on a real full-res CT.
- **`segment_threshold.py`** — segments the air lumen first, dilates by
  `BONE_WALL_THICKNESS_MM`, thresholds bone within that shell. Component
  selection: closest blob to the canal axis line.
- **`segment_dl.py`** — Stage B hook, currently a stub, always calls
  Stage A.
- **`pinna_landmarks.py`** — `PinnaLandmarks`: just `ear_center` + side.
- **`segment_pinna_threshold.py`** — segments the outer **skin** surface
  (no reliable CT cartilage contrast exists — confirmed via literature).
  Component selection is nearest-*voxel* distance, not center-of-mass.
  `_remove_boundary_spike()` handles a sphere-tangent-to-scalp cosmetic
  artifact.
- **`mesh_export.py`** — `label_map_to_mesh()` (RAS mesh + `fill_holes()`
  additive patch); `export_mesh()` writes LPS-flipped vertices
  (`loadModel()` always assumes LPS — raw `trimesh.load()` callers must
  flip back to RAS); `decimate_to_target_resolution()` (quadric decimation
  + fragment repair, pinna call sites only); `label_map_to_mesh_subvoxel()`
  (scutum bone-wall pipeline only).
- **`mesh_isolate.py`** — vertex-adjacency flood-fill from a seed point,
  drawn loop as barrier. Raises `LoopDoesNotSeparateError` on a >90%-
  reached fill (genus>0 mesh, not a bad drawing). `crop_toward_canal()` +
  `keep_connected_component_containing()`.
- **`postprocess.py`** — speck removal, hole fill, smoothing,
  `close_small_tunnels()` (closing-only, additive).
- **`curvature/`** — file-for-file port of Curvature Project v4:
  `descriptors.py` (pymeshlab → `trimesh.curvature` + hand-rolled cKDTree
  edge lookup), `geodesics.py` (potpourri3d → scipy Dijkstra over mesh
  edges — systematically a little long but consistent across candidates),
  `registration.py` (open3d → `trimesh.registration.icp`, point-to-point),
  `heatmap.py` (matplotlib → hand-rolled ColorBrewer lerp). `pipeline.py`
  is the callable entry point.

---

## Wizard/Slicer-specific notes

- **Lazy page-controller imports**: `.ui` files load upfront; controller
  *modules* import only on first navigation — lets Setup's dependency
  install run before anything imports trimesh/SimpleITK-dependent code.
- **Landmark placement**: Slicer Markups fiducial nodes, one point per
  click.
- **Review pages**: slider(s) → Run → pull volume → coarse crop → precise
  ROI → segment → postprocess → push back as labelmap + segmentation node
  + exported model.
- **Draw pages**: Start Outline uses
  `SetCurveTypeToShortestDistanceOnSurface(modelNode)` (NOT
  `SetAndObserveSurfaceConstraintNode` alone — that only registers the
  node). Seed point → Isolate Patch → `mesh_isolate` → export → load as new
  model.
- **Scutum outline ruler** (`page_scutum_draw.py`): optional 2-point
  `vtkMRMLMarkupsLineNode` scale reference, colored blue
  (`SCUTUM_RULER_COLOR`). Auto-hidden once Isolate Patch succeeds.
  **Confirmed working.**
- **Verify page**: two explicit approval checkboxes, deliberate manual gate
  before the final comparison.
- **Tutorial mode**: `WizardState.tutorial_mode`, read via
  `base_page.set_tutorial_text()`; Welcome page skipped in Normal mode via
  `wizard_state.SKIP_PAGE_IF`.
- **Setup page's `restartSlicerButton`**: calls `slicer.util.restart()`,
  normally shown only when a fresh package install needs a real restart.
  Currently **forced always-visible** for the anisotropy-bug retest cycle —
  see "Known open issues" #6 to revert.
- **`base_page.WizardPage.run_blocking()`**: runs a heavy `core/` call on a
  background thread while the main thread polls
  `slicer.app.processEvents()`, keeping Slicer's UI responsive (safe
  because `core/` is Slicer/VTK-independent by design). Wired into pinna
  review, pinna draw/isolate, and scutum review's heavy pipeline steps,
  each with a `progressBar`. The Curvature page instead parses its own
  progress-string callback into a real `QProgressBar` rather than
  backgrounding (its callback touches Qt directly). **Not yet real-Slicer
  tested for any of these.**

---

## Curvature integration

Runs fully in-process (no subprocess/separate venv) — pymeshlab/
potpourri3d/open3d each swapped for numpy/scipy/trimesh equivalents.
Verified only against synthetic meshes (sphere curvature sanity check,
scoring discrimination, end-to-end smoke test). **Not validated**: whether
the 300-candidate pipeline always ranks the true best real-anatomy site #1
(a known limitation of the original algorithm's design, not introduced by
the port).

UI (2026-07-29): progress streams to `statusLabel` (no scrolling log — 
surgeons don't need a terminal-style log). Results table has a "Locate"
column — Show/Hide buttons toggle control points on a shared
`harvest_site_markup_node` (magenta, positioned from the heatmap mesh's own
polydata, not re-parsed CSV coordinates). Tutorial text explains Chamfer
(avg post-alignment mismatch, main ranking number) vs. Hausdorff (single
worst mismatch, flags a local bad spot). Download step is its own final
page (`page_complete.py`, table-driven `_DOWNLOADABLE_FILES`, 4 files:
heatmap, scutum defect, pinna isolated mesh, scutum bone wall mesh; each
checkbox auto-disables if its file was never generated). Not yet
real-Slicer tested.

---

## Pinna performance work (2026-07-29/30) — summary, all confirmed

Thomas reported pinna segmentation "really slow" on a fine-spacing scan
(0.173mm). Fixes, in order:

1. **`roi_crop` mask-building** built one whole-volume physical-coordinate
   array before evaluating the ROI test — rewritten to loop per-z-slice
   (`_build_mask_by_slices`), near-linear instead of super-linear. Verified
   bit-identical output. **Confirmed real speedup.**
2. **No mesh decimation anywhere** — a fine-spacing pinna scan (large
   surface area) balloons `marching_cubes` output to hundreds of thousands
   of vertices, which also slows `mesh_isolate.py`'s networkx flood fill
   (canal stays fast regardless of spacing since its ROI has small surface
   area). Fixed: `mesh_export.decimate_to_target_resolution()` (quadric
   decimation via `fast-simplification`, target `PINNA_MESH_TARGET_EDGE_MM`
   = 0.3mm), applied at pinna's 3 call sites only — not scutum, where
   sub-voxel precision would be blurred, and canal meshes were never slow.
3. **Component-selection fallback** (`_closest_component_to_point`'s
   full-array EDT when the landmark voxel isn't itself labeled) — naive
   KD-tree over all foreground voxels was a weak win on real (dense) scans;
   shipped fix builds the KD-tree over SURFACE voxels only, since the
   nearest point to something outside every component can never be
   interior. **Confirmed: 7.30s → 0.99s on real re-test.**
4. **Tight-bounding-box crop** (`roi_crop.crop_to_own_bounding_box`, 15mm
   margin) — correctness-verified but real-scan benefit turned out
   negligible (real pinna anatomy fills nearly the whole ROI sphere's
   bounding box). Kept in (provably harmless), but don't count on it.
5. **UI freeze ("Not Responding")** during these multi-second calls — fixed
   via `run_blocking()` (see above). **Not yet real-Slicer tested** for
   responsiveness itself.

Remaining Stage A cost (~33s, down from ~45s) is now concentrated in
`smooth_boundary` (~15s) and `label_map_to_mesh`'s marching_cubes (~20s),
both voxel-count-bound with no further free win — further speedup would
need an actual quality tradeoff (lower `PINNA_ROI_RADIUS_MM`, fewer
smoothing iterations, `step_size>1`), none of which should be pursued
without asking Thomas first.

---

## Pinna mesh anisotropy tear (2026-08-22, fix shipped 2026-08-25, provisionally confirmed)

A real scan at extreme Z-anisotropy (0.39/0.39/2.5mm, ~6.4:1 ratio, 32
slices) produced a blocky, holey pinna mesh — a genuinely single-connected
voxel mask torn into a non-watertight, multi-component mesh by
marching_cubes on badly-anisotropic data (every prior real scan tested was
near-isotropic). Thomas independently confirmed the mechanism by manually
resampling the whole scan to isotropic *before* running the pipeline and
getting a correct result — proving the fix had to happen much earlier than
"right before marching_cubes," since thresholding/component-selection/
postprocess's own voxel-radius morphology were all still running on the
bad native grid first.

**Fix**: `io_utils.resample_to_bounded_anisotropy(image, max_ratio,
is_label)` — triggers when the coarsest-axis spacing exceeds
`config.MESH_MAX_ANISOTROPY_RATIO` (2.0) × the finest-axis spacing, then
resamples the coarse axis all the way to match the finest (not just down to
the trigger ratio — an earlier, less-aggressive version wasn't enough).
Handles both binary masks (resample + re-threshold at 0.5) and grayscale
(linear interpolation). Called as early as possible in all 3 pinna
pipeline flows (right after coarse crop, before any thresholding/
morphology), not just once before meshing.
`mesh_export.label_map_to_mesh()` keeps its own late-stage call too, now
usually a no-op (defense-in-depth). Deliberately did NOT switch to
always-resample-every-scan-to-fixed-spacing — Thomas recalled an earlier
version doing that and getting worse results; this only ever touches a
scan that's already badly anisotropic.

A separate, real bug was found and fixed in the same investigation:
decimation tearing thin folded anatomy into unwatertight slivers — fixed by
`mesh_export._repair_decimation_fragments()` (drops post-decimation
components smaller than `DECIMATION_FRAGMENT_MIN_TRIANGLES`, then
`fill_holes()`). Real, synthetically confirmed, kept — but turned out NOT
to be what Thomas was seeing on the anisotropy scan (decimation no-opped
there). Don't confuse the two.

**Status**: confirmed "much better" on the one real scan that showed this.
Provisional — Thomas wants more scans tested before closing. Leave the
TEMPORARY diagnostic prints and the forced-visible `restartSlicerButton` in
place until confirmed (see "Known open issues" #6).

---

## Interactive Segment Editor threshold rework (2026-07-30, confirmed working)

Replaced `page_scutum_review.py`'s custom SimpleITK threshold pipeline
(sliders → `segment_dl.segment()` → shell-restricted
`segment_threshold.segment_bone_wall()`) with an embedded, live
`qMRMLSegmentEditorWidget` (Slicer's own Threshold/Paint/Erase/Islands/
Smoothing effects). Motivation: 4 rounds of sheetness/CurvatureFlow tuning
(see "Improving Canal Segmentation" below) never fixed the thin-wall/
scutum-malleus complaints, and Thomas found Slicer's own interactive
Threshold consistently more accurate by hand.

**Flow**: "Auto-Calibrate & Segment" applies a starting threshold (seed
calibration or `DEFAULT_BONE_THRESHOLD`) via the Threshold effect → live
manual adjustment (Threshold/Paint/Erase/Islands/Smoothing) → "Preview 3D
Result"/Next finalizes: pulls the segmentation's labelmap, coarse-crops,
builds the precise cylinder ROI, keeps the component nearest the canal
axis, runs `postprocess.run_full_postprocess(..., close_tunnels=True)`,
meshes. Downstream state (drawn outline, verify, heatmap) clears at
finalize time, not on every threshold tweak.

Known deliberate tradeoffs: nearest-component selection can discard a
disconnected manual paint addition (use Islands first); sub-voxel mesh
extraction no longer applies once Paint/Erase can edit the mask, falls back
to plain `label_map_to_mesh()`; `segment_dl.py`/`segment_threshold.py`
(sheetness included) are no longer called by this page but kept for a
possible future Stage B model.

**Confirmed working**: noticeably more accurate than the old pipeline; the
one-click-calibrate + live-adjust automation level feels right. Also
confirmed several previously-unverified Slicer APIs: embedding
`qMRMLSegmentEditorWidget` in a page's own `.ui`,
`setEffectNameOrder`/`unorderedEffectsVisible`, driving Threshold via
`setParameter`/`onApply()`.

**Calibration-seed cleanup (2026-07-31)**: dead `air_seed` (scutum page)
deleted. Pinna review page got a real 2-point calibration it never had
before (air + soft-tissue → skin threshold) — see "Calibration" under
Current Status above. `core/threshold_seeds.py`'s old 3-point
`ThresholdSeeds` removed in favor of each page owning its own field(s) on
`WizardState` (`pinna_air_seed`, `pinna_soft_tissue_seed`,
`scutum_bone_seed`).

---

## Scutum finalize mesh quality (2026-07-31) — ACTIVE, pick up here

Thomas reported "Preview 3D Result" visibly degrades an already-good,
hand-tuned live segmentation vs. what the Segment Editor widget itself
shows.

- **Round 1** (shipped, partial fix): disabled `postprocess.
  smooth_boundary()` for scutum, cut Laplacian iterations to 2. Shape
  preserved better, but "too blocky."
- **Round 2** (shipped, REGRESSION, reverted): swapped Laplacian for Taubin
  smoothing. Still blocky AND now wrong shape too. Lesson: vertex-space
  smoothing after marching_cubes only trades blockiness against shape
  distortion on the same knob — can't fix both.
- **Round 3** (shipped, never tested, superseded): blurred the mask itself
  (Gaussian) before marching_cubes instead of smoothing vertices after —
  synthetically beat blockiness AND preserved detail. Superseded by round
  4's different diagnosis before ever being tested.
- **Round 4 (current, shipped, NOT real-Slicer tested)**: root cause
  reframed — Slicer's live 3D view is its own `vtkDiscreteFlyingEdges3D`→
  `vtkWindowedSincPolyDataFilter` closed-surface generator, while finalize
  was re-deriving a completely separate mesh via our own crop/postprocess/
  marching_cubes. Two different algorithms on similar-but-not-identical
  data — why 3 rounds of tuning our own pipeline never converged on what
  Slicer's already looked like. **Fix**: finalize now pulls the
  segmentation's own closed-surface mesh directly
  (`ExportVisibleSegmentsToModels` → polydata → trimesh), then does the
  ROI-crop and nearest-axis-component selection in MESH space instead of
  voxel space (`mesh_export.crop_mesh_to_vertex_mask()`,
  `select_mesh_component_nearest_axis()` — both synthetically validated as
  matching the old voxel-space logic bit-for-bit where equivalent).
  - **EXPERIMENTAL, Thomas's explicit call**: `postprocess.
    run_full_postprocess()` (including `close_small_tunnels()`) no longer
    runs at all for scutum finalize — a real risk given the canal region's
    tube-like topology and the Isolate Patch saga's history. Thomas chose
    to test this empirically rather than keep the safer alternative (push
    the cleaned mask through a temp segmentation node so Slicer meshes
    it).
  - **Both checklist items CONFIRMED on 1 real scan (2026-08-31, after the
    round 5 fix below)**: (1) finalized mesh matches the live Segment
    Editor view; (2) Isolate Patch on the scutum draw page works on this
    mesh without hitting `LoopDoesNotSeparateError`, despite no tunnel-
    closing postprocess running. Needs more scans before trusting this
    generalizes — see round 5 and "Current status" above.
- **Round 5 (2026-08-31, shipped, CONFIRMED on 1 real scan)**: first
  real-Slicer test of round 4 hit step (1) before even reaching it —
  finalize reported success but the 3D view was empty. See "Current
  status" above for the confirmed root cause (whole-volume threshold + no
  cleanup produced 1054 mesh components in the ROI crop on that scan;
  `select_mesh_component_nearest_axis()` had no size weighting and could
  land on any of the 1053 debris fragments instead of the real
  2416.34mm² wall) and the fix (`min_area_mm2` floor,
  `config.SCUTUM_MESH_COMPONENT_MIN_AREA_MM2` = 5.0mm², plus
  `[mesh_export diag]` prints). Confirmed via diagnostic console output on
  a re-run of the same scan (not just "it looked fine this time") — chose
  the correct large component, rendered correctly, matched the live
  Segment Editor view. Needs more scans before the 5.0mm² threshold and
  general approach are trusted (component count/debris severity likely
  scan- and threshold-dependent).

---

## Improving Canal Segmentation — sheetness stalled, exploring alternatives

Multi-session push on `segment_threshold.py`, motivated by: thin-bone
precision, scutum/malleus reading as fused, missing wall chunks near the
tympanic membrane.

**Shipped, confirmed working:**
- **Seed-based threshold calibration** (superseded structurally by the
  2026-07-31 rework, calibration math unchanged) — synthetic 150-patient
  test showed real benefit under simulated scanner drift.
- **Thin-wall warning** (`core/wall_quality.py`, advisory only) — synthetic
  diagnostic found segmentation failures correlate with true wall
  thickness (r=+0.83), not lumen accuracy or noise — a genuine resolution
  limit, not fixable by threshold tuning. `check_wall_thickness()` warns
  below `MIN_SAFE_WALL_THICKNESS_MM` (1.0mm).
- **Sub-voxel mesh extraction** (`label_map_to_mesh_subvoxel`, scutum-only,
  not used for hand-edited masks) — extracts from the smoothed grayscale
  field near the boundary instead of the binarized mask, guarded against
  reopening postprocess-closed tunnels. ~15-20% RMS boundary-error
  reduction.
- **Gaussian → CurvatureFlow smoothing** (`core/smoothing.py`,
  scutum/canal only) — fixed the scutum/malleus fusion: CurvatureFlow
  preserves a true thin air gap where Gaussian blur pushes it above
  threshold. **Confirmed fixed on a real scan**, no wall-quality
  regression.

**Hessian sheetness enhancement** (`core/sheetness.py`,
`ENABLE_SHEET_ENHANCEMENT`, currently `True`) — **4 rounds of real-scan
A/B, still not confirmed helping**. Attacks thin-wall recall + gap
preservation via local shape (bright/dark sheetness), gated by intensity
margins so it can never touch a voxel far from `bone_threshold`. Round 1
was worse than plain thresholding (no intensity floor; fixed via
`BRIGHT/DARK_SHEETNESS_INTENSITY_MARGIN_HU`). Round 2 fixed scales/gamma
that had been implicitly calibrated to a uniform 0.3mm resolution the
pipeline never actually guarantees. Round 3: still no improvement. Round 4
replaced the fixed noise-gate gamma with a self-calibrating one (Krcah et
al. bone-sheetness design), validated as portable across two synthetic
scan scales without retuning — **still no noticeable real improvement**.
Confirmed the code is actually being applied. Diagnostic logging
(`shell_voxel_count`, response fractions) was added but **nobody has looked
at that output yet — the immediate next step if this feature is
revisited**, before concluding anything further.

**Research done instead of more blind tuning (2026-07-29)**: literature
confirms even funded academic groups hit the same resolution wall on thin
structures (published stapes Dice as low as 0.56-0.70). Most promising
alternative: atlas-based/active-shape-model registration (imports shape
info from a higher-resolution reference) — Vanderbilt's Noble/Dawant
approach, Dice>0.8 on temporal bone structures. The ABL Slicer extension
(pretrained DL) was evaluated and **rejected** for deployment reasons
(GPU/Docker/VRAM too close to Thomas's hardware ceiling, PHI concern with
their remote-server inference) — not a technical rejection.

**Chosen direction: multi-atlas registration + label fusion** (SimpleITK
registration + `sitk.STAPLE()`/`LabelVoting()` as a soft prior, feeding the
existing `segment_dl.py` Stage B stub) — **BLOCKED, in progress**: OpenEar
(candidate atlas, adult cadaveric CBCT+micro-CT) has no canal-wall label
and is adult-only, while Thomas's patients skew pediatric — pediatric
temporal bone anatomy differs qualitatively (canal floor is unossified
cartilage until ~age 3-4), so an adult atlas risks a false "solid bone"
prior. Searched for a pediatric equivalent: Seattle Children's atlas is
view-only; a promising paper's public GitHub release was checked directly
and does **NOT** actually contain the EAC label its paper claims — a
reminder to verify a dataset's real contents, not just trust the abstract.
**Open decision, needs Thomas + his supervisor**: (1) use Thomas's own
consented local pediatric cases as a private, never-committed atlas, (2)
keep searching/negotiating for public pediatric data, or (3) fall back to
adult-only OpenEar with an age-gated cutoff. **Do not assume a direction
next session.**

**Dead ends, don't re-suggest**: pure intensity-based bone segmentation
with no shell restriction (would grab mastoid/ossicles); epitympanum
inclusion via reusing the `near_eardrum` landmark's proximity
(synthetically clean, but caused real widespread wall dropout on an actual
scan — if revisited, use a dedicated 3rd landmark inside the epitympanum,
not a proximity guess).

**Standing lesson**: synthetic tests here have repeatedly validated the
right *mechanism* while still failing to predict real-scan outcome, in both
directions (epitympanum: clean synthetically, broke real scans; sheetness
gamma: an easy synthetic test looked airtight, then failed at a harsher,
more realistic noise level). Always retest constants against a stress case
at the real decision boundary — and treat "passed synthetic testing" as
necessary, not sufficient, before telling Thomas something is fixed.

---

## Known open issues

1. **Reset All Points (scutum landmarks)** — reported broken, never
   debugged.
2. **No `.s4ext`/CMakeLists** — dev-only scripted module (Additional Module
   Paths), no packaging scaffold, not needed yet.
3. **Pre-DICOM-load crash** — probably fixed (2 speculative fixes), never
   explicitly reconfirmed, hasn't recurred.
4. **Several Slicer API calls "correct per docs, never runtime-confirmed"**:
   `SetCurveTypeToShortestDistanceOnSurface`,
   `ImportLabelmapToSegmentationNode`/`ExportVisibleSegmentsToLabelmapNode`,
   Segment Editor widget attribute names, pinna-first-reorder hide/show
   logic, `slicer.util.restart()`, `run_blocking()`'s core threading
   assumption (faked-processEvents test only, not real Qt/VTK).
5. ~~Embedded `qMRMLSegmentEditorWidget`~~ **CONFIRMED working**
   (2026-07-30) — see Segment Editor rework above.
6. **Once pinna anisotropy fix is confirmed across more scans**: revert
   `page_setup.py`'s forced-visible `restartSlicerButton`
   (`setVisible(True)` → `False` in the `if not missing:` branch) and
   remove the TEMPORARY `[mesh_export diag]`/`[pinna diag]` prints in
   `core/mesh_export.py`, `page_pinna_review.py`, `core/io_utils.py`.

---

## Key lessons (read before touching volume/mesh coordinate code)

- **RAS/LPS mismatch** (hit twice): `sitkUtils.PullVolumeFromSlicer()`
  returns LPS while Markups landmarks are RAS — `io_utils.flip_ras_lps()`
  after pulling and before pushing back. Separately, `slicer.util.
  loadModel()` always assumes a loaded STL is LPS — `mesh_export.py`
  writes LPS-flipped vertices; any code loading the same file via raw
  `trimesh.load()` must flip back to RAS. Don't dismiss VTK/Slicer
  coordinate-system warnings.
- **Curve surface constraint**: `SetCurveTypeToShortestDistanceOnSurface
  (modelNode)` is required; `SetAndObserveSurfaceConstraintNode` alone does
  nothing.
- **Isolate Patch saga** (8 rounds, resolved 2026-07-28): root cause was
  (a) unconditional re-postprocessing on every "Next" eroding an
  already-good mesh — fixed by gating reprocessing behind an explicit "was
  this actually edited" flag — and (b) genuine topological handles from the
  canal's own tunnel anatomy. Final fix: run `crop_toward_canal()` **before**
  isolation so canal-adjacent tunnels are severed first. Lesson: trust a
  precise user before/after observation over further algorithmic
  theorizing.
- **Absolute point size**: Markups `GlyphScale` is a screen-percentage
  recomputed from a possibly-stale camera scale — use `UseGlyphScale(False)`
  + fixed mm `GlyphSize`, not a recenter (which would also reorient the
  camera).
- **Segment Editor edits `vtkMRMLSegmentationNode`s, not plain labelmaps**
  — convert to a real segmentation node on Run.
- **Memory blowup**: `build_roi_mask`/`build_spherical_roi_mask` must
  always be preceded by a coarse rectangular crop on real volumes.
- **Synthetic tests validate mechanism, not real-scan outcome** — hit
  repeatedly (epitympanum, CurvatureFlow, sheetness gamma twice, decimation
  topology). Real-Slicer feedback from Thomas is the only way to catch what
  synthetic tests don't model. Still always test synthetically first — it
  does catch real bugs before they ship — just don't call something
  "fixed" until Thomas confirms on a real scan.

---

## Dead ends — don't re-suggest

- **4-point tilted-plane ROI** — required imprecise plane-side clicks,
  caused real bugs. Replaced by 2-point perpendicular-plane ROI; widen
  `ROI_AXIAL_MARGIN_MM` instead if perpendicular planes ever clip anatomy.
- **MedSAM2** — GTX 1060 6GB not viable for its VRAM needs. Revisit only if
  hardware changes.
- **nnU-Net** — still the eventual Stage B plan, but no training data
  banked (`MIN_CASES_TO_TRAIN=15`). Not urgent.
- **Cartilage-specific CT segmentation** — no reliable CT contrast exists
  per literature (MRI-only). This is why pinna Stage A segments skin, not
  cartilage.
- **Installing Curvature Project v4's original deps (pymeshlab/
  potpourri3d/open3d) into Slicer's Python** — rejected (compiled-extension
  risk), superseded by the numpy/scipy/trimesh port.
- **Subprocess/separate-venv bridge to standalone Curvature Project v4** —
  worked, but required a second Python install, violating the
  zero-Python-experience constraint. Replaced by the in-process port.
- **Pure intensity-based bone segmentation (no shell restriction)** and
  **epitympanum inclusion via `near_eardrum` proximity** — see "Improving
  Canal Segmentation" above.

---

## Testing approach

`core/` has zero Slicer dependency — validate new logic with synthetic
NumPy/SimpleITK volumes and trimesh meshes first (this has caught most bugs
in this project). There is no Slicer install in the dev environment —
real-Slicer console tracebacks (pasted verbatim by Thomas) are the only way
to catch what synthetic testing can't. WebSearch/WebFetch actual Slicer API
source/docs before trusting assumed API behavior — has repeatedly found
real bugs.
