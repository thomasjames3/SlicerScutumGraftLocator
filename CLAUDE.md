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

**One active, unconfirmed thread to pick up first in a new session:**
1. **Scutum finalize mesh quality, round 4** — see below, unchanged from
   before.

**Pinna mesh anisotropy tear (2026-08-22, resolved 2026-08-25) —
confirmed fixed on Thomas's first real failing scan, but he still wants
to test more scans before fully closing this out; treat as
provisionally resolved, not yet a closed issue.** Full trail in "Pinna
mesh decimation topology bug" below. Short version: a real scan at
extreme Z-anisotropy (0.39/0.39/2.5mm, ~6.4:1 ratio, only 32 slices) was
tearing a genuinely single-connected voxel mask into a non-watertight,
multi-component mesh. The first fix (upsample the coarse axis right
before `marching_cubes`, when the ratio exceeds `config.
MESH_MAX_ANISOTROPY_RATIO = 2.0`) fired correctly but **was not enough
on its own** -- a per-component diagnostic added afterward showed the
2nd component was a real torn-off piece of anatomy (188 verts, 18mm²,
its own fully watertight sub-mesh), not decimation-style debris, so it
couldn't just be dropped. Thomas independently confirmed the actual
mechanism by manually resampling the whole scan to isotropic *before*
running the pipeline and getting a correct result -- proving the fix
needed to happen much earlier than just before meshing, since
thresholding/connected-components/spike-removal/`postprocess`'s own
voxel-radius-based morphology were all still running on the
badly-anisotropic native grid first. **Real fix shipped**: new
`io_utils.resample_to_bounded_anisotropy()` (same "only intervene once
anisotropic enough, then resample fully to isotropic" logic, generalized
to also handle grayscale intensity data, not just binary masks) is now
called as early as possible in each of the 3 pinna pipeline flows --
right after the coarse crop for a fresh segmentation, right after
pulling the labelmap for a Segment-Editor-edited or Isolate-Patch-
fallback re-derive -- instead of only once, right before
`marching_cubes`. Deliberately did NOT switch to always-resample-every-
scan-to-a-fixed-spacing (Thomas recalled an earlier version of this
program did that and got worse results) -- this only ever touches a scan
that's already badly anisotropic; an already-near-isotropic scan is
untouched. **First real-Slicer re-test (2026-08-25): Thomas reports the
result is "much better."** Still open: more scans need testing before
this is fully confirmed general -- until then, leave the TEMPORARY
diagnostic prints and the forced-visible `restartSlicerButton` in place
(see "Known open issues" item 6).

An EARLIER, separate real bug was also found and fixed in the same
investigation (decimation corrupting thin folded anatomy) -- genuinely
real, kept, synthetically confirmed -- but turned out NOT to be what
Thomas was seeing on his test scan (decimation no-opped there). Don't
confuse the two fixes.

Entire scutum and pinna pipelines confirmed working end-to-end in real
Slicer as of 2026-07-28/29: DICOM load → scutum landmarks → pinna
landmarks/review/draw → scutum review/draw → (Verify, Curvature not yet
runtime-tested). **That confirmation predates a substantial round of
2026-07-30 changes to the pinna review/draw and scutum review pages**
(see "Pinna segmentation performance" and "Pinna UI responsiveness fix"
below) — mesh decimation, a component-selection bug fix, a tight-bbox
crop, and background-thread + progress-bar wiring in
`page_pinna_review.py`/`page_pinna_draw.py`/`page_scutum_review.py`. Each
piece was validated individually (synthetic tests, and Thomas re-running
Stage A/isolate specifically to confirm timing), but the full pages
haven't had a fresh end-to-end real-Slicer pass since all of it landed —
don't assume "confirmed working end-to-end" still covers these three
pages' current code without checking whether anything's been touched
since 07-28/29.

**`page_scutum_review.py` was substantially rewritten again, same day
(2026-07-30), superseding the above for that page specifically** — see
"Interactive Segment Editor threshold rework" below. The old slider+Run
threshold pipeline was replaced with an embedded, live
`qMRMLSegmentEditorWidget` (Slicer's own Threshold/Paint/Erase/Islands/
Smoothing effects, used directly in-page). **First real-Slicer test
(2026-07-30, same day): core operations confirmed good** — Thomas
reported the actual segmentation is noticeably more accurate than the old
automated pipeline, and the level of automation (one-click calibrate,
live adjust) feels right. The "some interface fixes still needed, not yet
specified" note that used to be here is now answered — see "Scutum
finalize mesh quality" below for what it turned out to be (the "Preview
3D Result" button visibly degrading an already-good hand-tuned
segmentation). **Round 4 (2026-07-31, same day)** replaced the whole
crop/postprocess/marching_cubes rebuild with pulling the segmentation's
own already-good closed-surface mesh directly — root cause diagnosis and
implementation done, **not yet real-Slicer-confirmed**, including a
deliberately-accepted open question (does skipping postprocess entirely
still work OK with the draw page's Isolate Patch step?) that Thomas
wants tested empirically rather than guessed at. **This is the active,
unfinished thread — pick up here first in a new session.**

**Calibration-seed rework across pinna review + scutum review pages
(2026-07-31)** — see "Calibration-seed cleanup" under "Interactive
Segment Editor threshold rework" below for the full history. Summary:
removed a dead, silently-discarded air calibration point from the scutum
page; added a real 2-point (air + soft-tissue) calibration to the pinna
review page, which never had one before; one bad design (a guessed
fixed-offset formula) was tried, real-Slicer-tested, found wrong, and
replaced with a real air click + midpoint (same mechanism the existing
bone-threshold calibration already uses successfully). **Confirmed
directionally correct on a real scan but not exact** (predicted -465 HU,
Thomas's own hand-tuned working value was ~-550) — Thomas explicitly
deferred closing that gap until more scans are tested, so the plain
midpoint (no extra margin) is what's currently shipped; don't add a
correction constant unprompted.

**Two more calibration bugs found and fixed, same feature, later same day
(2026-07-31) — both RESOLVED, confirmed by Thomas:**
1. **Scutum page's coarse crop rejected valid calibration points.**
   `page_scutum_review.py`'s `_on_calibrate_clicked` sampled the bone/
   soft-tissue seed points against a crop box built from only the 2 canal
   landmarks (±15mm margin, `roi_crop.crop_to_landmark_region`) — a box
   sized for the OLD shell-restricted mask-building pipeline, not for
   containing arbitrary surgeon clicks elsewhere. A bone point placed
   legitimately on the scutum (further than 15mm from the canal axis)
   fell outside it, raising a spurious "calibration point fell outside
   the scan region" error. **Fix**: new `roi_crop.crop_to_points_region()`
   builds the crop from the landmarks AND both seed points together, so a
   point can never fall outside a box it directly helped define. Confirmed
   this resolved it ("this is working well now"). Worth revisiting later:
   if a scutum point is ever THAT far from the canal axis, the *actual*
   segmentation ROI cylinder (10mm radius from that same axis,
   `INITIAL_ROI_DIAMETER_MM`/`ROI_AXIAL_MARGIN_MM`) may also not reach it
   — flagged to Thomas, not yet independently confirmed either way.
2. **"Auto-Calibrate always gives 100 no matter where the marker is
   placed" — root-caused to NOT be a code bug.** Live-Slicer console
   checks (`w = slicer.modules.earreconstructionplanner.
   widgetRepresentation().self(); print(w.state...)`) confirmed
   `scutum_bone_seed` was set correctly, but `pinna_air_seed`/
   `pinna_soft_tissue_seed` were both `None` AND the pinna page's
   calibration fiducial node had 0 control points — the pinna review
   page's 2-point calibration sequence (open air, then soft tissue) had
   simply never been completed for this scan; Thomas had been typing a
   threshold into that page's slider directly instead. Since
   `calibrate_bone_threshold()` needs BOTH the scutum bone point and the
   pinna soft-tissue point, missing either one silently falls back to
   `config.DEFAULT_BONE_THRESHOLD` (100) with no error — which is exactly
   what was observed. **Fix, agreed with Thomas**: rather than leave this
   as a silent gotcha, `page_pinna_review.py`'s `on_leave_next()` now
   BLOCKS "Next" until both calibration points are placed (previously
   optional-by-design — a real, deliberate policy change, not just a
   messaging fix). The Skin threshold slider itself remains fully
   surgeon-adjustable either way; only PLACING the two points is now
   required. Every user-facing string that used to imply the slider was
   an equivalent substitute (tutorial text, the per-step instructions,
   the Redo-calibration message, the module docstring) was updated to say
   "Required" and to explicitly clarify the slider stays editable
   afterward, per Thomas's follow-up request. See "Calibration-seed
   cleanup" below for the code-level detail.

**Not yet exercised in real Slicer:** Verify page; Curvature page's Qt
widgets (progress streaming, results table, heatmap vertex-color
display, and the new progress-bar parsing added 2026-07-30 -- see "Pinna
UI responsiveness fix"). The curvature *algorithm* itself (in-process
port) is verified against synthetic meshes, just not the Slicer-side UI
plumbing around it.

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
│   │   ├── io_utils.py               # DICOM/volume load; flip_ras_lps(); resample_to_isotropic() unused; resample_to_bounded_anisotropy() (pinna anisotropy fix)
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
- **Scutum outline ruler (2026-07-30, `page_scutum_draw.py`)**: a
  `toggleRulerButton` ("Show Ruler"/"Hide Ruler") creates a 2-point
  `vtkMRMLMarkupsLineNode` the surgeon can place on the bone-wall surface
  as a physical scale reference while tracing the defect outline —
  Slicer's own line-markup distance label does the measurement, no custom
  math needed. First click creates the node and enters Place mode for its
  2 points (persistence 1, same as the outline curve — Slicer exits Place
  mode on its own once both points are placed); later clicks just toggle
  the existing line's visibility rather than recreating it, so a
  measurement isn't lost while decluttering the view. Colored
  `config.SCUTUM_RULER_COLOR` (blue) specifically so it can't be confused
  with the outline curve's own default Markups color (yellow/green) —
  the outline itself was deliberately left untouched (no explicit color
  set) to avoid changing its established look. Auto-hidden (not removed)
  once Isolate Patch succeeds, matching how the outline curve and seed
  point are also hidden at that point. **Confirmed working in real
  Slicer (2026-07-30)** — Thomas reported the ruler works.
- **Verify page**: two explicit approval checkboxes, deliberate manual
  gate before the final comparison.
- **Tutorial mode**: `WizardState.tutorial_mode`, set once on Setup, read
  everywhere via `base_page.set_tutorial_text()`. Welcome page
  (tutorial-only) skipped in Normal mode via `wizard_state.SKIP_PAGE_IF`
  (a plain state predicate checked in `_show_page()`, so skip-checking
  never forces an early controller import).
- **Restart Slicer button (Setup page, 2026-07-30)**: freshly
  `pip_install`'d packages (especially ones with compiled/binary
  components, e.g. `fast-simplification`) aren't always reliable to use
  immediately in the same already-running Slicer process — Windows in
  particular can't always safely replace/re-initialize a DLL that's
  already loaded. `page_setup.py`'s `restartSlicerButton` calls
  `slicer.util.restart()` (with a confirm dialog first, since it closes
  the whole app) and is only ever shown when `_refresh_status()` finds
  missing packages — i.e. hidden again on a later launch once everything
  installed successfully in a prior, properly-restarted session, so a
  returning surgeon on an already-set-up Slicer never sees an unexplained
  restart option. Safe to offer unconditionally when shown, since Setup
  is page 0 — no DICOM/landmarks/segmentation state exists yet to lose.

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

**Root cause found for BOTH the Stage A slowness and the draw/isolate-step
slowness (2026-07-30), without needing the `[pinna timing]` console output
above** -- Thomas separately reported the draw/isolate step (page_pinna_draw.py
-> core/mesh_isolate.py) is also slow on this same fine-spacing scan, which
pointed at a shared cause rather than two unrelated bottlenecks: **nothing in
this pipeline ever decimates/downsamples the mesh** --
`mesh_export.label_map_to_mesh()`/`label_map_to_mesh_subvoxel()` run
`skimage.measure.marching_cubes` with no `step_size` (defaults to 1, the
finest possible), and no simplification step follows. marching_cubes'
vertex count scales with (surface area)/(native spacing)^2. The ear canal's
ROI (a thin tube around 2 landmarks) has a small surface area regardless of
spacing, so its mesh stays small even at fine native resolution -- but
`PINNA_ROI_RADIUS_MM`'s 45mm sphere covers a much larger patch of skin, so
at fine native spacing (this scan's 0.173x0.173x0.2mm) the pinna mesh
balloons into the hundreds of thousands of vertices. That bloated mesh then
hits `core/mesh_isolate.py`'s `isolate_surface_patch()`, which builds a full
`networkx.Graph` (`mesh.vertex_adjacency_graph`) over every vertex before
doing anything else, plus a plain-Python `for i in range(len(mesh.vertices))`
loop and a `np.vectorize` call in `_submesh_from_vertices()` -- all three
scale with vertex count but with heavy per-element (Python-level) cost, so
they get disproportionately slow exactly when the mesh is large. This is
why canal stays fast (small mesh regardless of spacing) while pinna
segmentation-to-mesh AND drawing/isolation both slow down together on a
fine-spacing scan (same root cause, two places it shows up).

**Fix shipped: `mesh_export.decimate_to_target_resolution()`** (quadric-
error decimation via `trimesh.simplify_quadric_decimation()`, which wraps
the new `fast-simplification` dependency -- added to
`dependencies.py`'s `REQUIRED_PACKAGES`). Chosen over the two alternatives
discussed with Thomas -- capping `marching_cubes`' own `step_size`, or only
rewriting `mesh_isolate.py`'s networkx flood-fill onto `scipy.sparse` (no
resolution change at all, but doesn't address Stage A meshing time and is
higher-risk given `mesh_isolate.py`'s "8-round Isolate Patch saga" history)
-- because Thomas explicitly wanted whichever approach preserves shape
accuracy best: unlike `step_size` (which uniformly discards resolution
everywhere), quadric decimation preferentially keeps detail in
high-curvature areas (the helix) and removes it from flat ones (cheek/scalp
skin), for a given final vertex count.

Applied ONLY to the pinna pipeline -- three call sites
(`page_pinna_review.py`'s `_on_run_clicked()` and
`_refresh_mesh_from_segmentation()`, `page_pinna_draw.py`'s
`_build_fallback_mesh()`) -- immediately after `label_map_to_mesh()`, before
export/display/drawing, so the STL written to disk, the model node loaded
into Slicer, and the mesh the surgeon's curve gets constrained to are all
already decimated. Deliberately NOT applied to
`label_map_to_mesh_subvoxel()` or any scutum call site: that function's
whole purpose is sub-voxel boundary precision for the bone-wall mesh (see
"Sub-voxel mesh extraction" above), and decimating afterward would blur
exactly the improvement it exists to add -- also unnecessary, since the
canal mesh was never the slow one. Target density
(`PINNA_MESH_TARGET_EDGE_MM` in config.py) is set equal to
`TARGET_VOXEL_SPACING_MM` (0.3mm) -- not because that resampling actually
happens (it deliberately doesn't, see above), but because that's the
density scale the rest of this pipeline (mesh_isolate.py, draw-step point
sizes) was implicitly built and tested around on past scans that happened
to be near that native spacing. The function no-ops (returns the mesh
unchanged) whenever the mesh is already at or coarser than the target, so a
coarse-native-spacing scan's pinna mesh is untouched by this change.

**Synthetic validation (scratchpad, not committed)**: built the same bumpy
blob shape at 0.5mm and 0.173mm native spacing, ran real marching_cubes on
both. Confirmed the diagnosis directly -- the 0.173mm mesh had **8.4x** as
many faces as the 0.5mm mesh for the "same" shape. Decimating the fine mesh
down to `PINNA_MESH_TARGET_EDGE_MM` brought it to a comparable order of
magnitude to the coarse mesh's own face count (not identical -- the
target-face-count estimate is a geometric approximation based on total
surface area, expected to be in the right ballpark rather than exact).
Shape accuracy check (nearest-surface distance from every original fine-mesh
vertex to the decimated mesh) came back effectively 0.0mm -- expected,
since quadric decimation only removes/reconnects vertices, it doesn't move
the ones it keeps. As a proxy for the real `mesh_isolate.py` cost (without
needing a full Slicer session), timing `mesh.vertex_adjacency_graph`
construction (the same networkx call `isolate_surface_patch()` makes) on
the full-res vs. decimated fine mesh dropped from 4.3s to 1.4s from
decimation alone, before even considering the other savings from the
smaller mesh flowing through the rest of `mesh_isolate.py`'s Python-loop and
`np.vectorize` steps too.

**Not yet real-Slicer tested.** This should measurably speed up both pinna
Stage A (smaller mesh at the marching_cubes/export/loadModel/postprocess
steps that already had `[pinna timing]` instrumentation from the prior
session -- worth checking that output once more now, since the decimation
step will show up as a new line and the downstream steps' own times should
also drop from the smaller mesh flowing through them) and the draw/isolate
step, but the actual real-scan magnitude is unconfirmed -- consistent with
this project's repeated lesson that synthetic tests validate the mechanism,
not the real-scan outcome (see [[feedback_synthetic_tests_limits]]). If
Thomas reports the pinna's visible shape looks noticeably blockier/less
detailed after this change (unlikely at this target density given the
above, but worth ruling in/out explicitly), `PINNA_MESH_TARGET_EDGE_MM` is
the one knob to lower. The `mesh_isolate.py` networkx-to-scipy.sparse
rewrite discussed with Thomas as an alternative/follow-up was NOT done this
session -- still on the table as a further speedup if decimation alone
doesn't fully resolve the draw/isolate step's slowness.

**Second real-Slicer timing confirmed (2026-07-30, after the round-1
component-selection fix shipped)**: Thomas re-ran the same scan.
Stage A grand total dropped 45.41s -> 37.83s. `component selection`
dropped 12.91s -> 7.30s (the round-1 fix -- real, but far short of the
57.7x a synthetic test had predicted; see the "Fixed" note below for why,
and for round 3's much better fix). Isolate step also re-measured: TOTAL
5.00s (down slightly from 5.70s, consistent with the isolated mesh being
about the same size: 141,726 vertices this time vs. 155,816 before).
`barrier_graph subgraph + remove_nodes_from` (2.89s) is still the single
biggest isolate-internal cost, same "local radius covered the entire
mesh" situation as before (141,726 of 141,726 vertices within range) --
still not yet fixed, still low priority relative to Stage A.

**Real-Slicer timing confirmed (2026-07-30, same day): decimation IS
working correctly, but Stage A's slowness is dominated by steps decimation
can never touch.** Thomas ran a real scan (0.214844x0.214844x0.3125mm
spacing -- moderately fine, not as extreme as the 0.173mm case above) and
pasted the full `[pinna timing]`/`[isolate diag]`/`[isolate timing]` console
block (the newly-added isolate-step timing, see below). Findings:
- `label_map_to_mesh`: 503,296 verts/1,006,078 faces -> `decimate_to_target_
  resolution`: 260,031 verts/519,877 faces (1.67s). Decimation worked
  exactly as designed -- but only ~2x reduction here, not the 8.4x seen in
  the synthetic test, because this scan's native spacing is only
  moderately finer than `PINNA_MESH_TARGET_EDGE_MM` (0.3mm), unlike the
  more extreme 0.173mm case in Thomas's original slowness report -- less
  headroom to decimate down to by design (the function correctly never
  over-decimates below native detail).
- Stage A grand total: 45.41s. The three biggest line items --
  `component selection` (12.91s), `smooth_boundary` (15.61s), and
  `label_map_to_mesh`'s marching_cubes itself (19.82s) -- are ALL either
  bugs unrelated to mesh size, or costs that happen at the VOXEL level,
  before or during meshing. Decimation only ever shrinks the mesh AFTER
  marching_cubes runs, so it structurally cannot speed up marching_cubes'
  own cost, nor any postprocess step that runs before it. This is why
  Thomas "hasn't noticed any difference in speed" for Stage A -- decimation
  was never going to fix Stage A at all, only the isolate/draw step.
- Isolate/draw step: newly added `[isolate timing]` instrumentation (this
  function previously only had diagnostic prints, no actual timing) showed
  `isolate_surface_patch` TOTAL = 5.70s, with `vertex_adjacency_graph
  build` (1.44s) and `barrier_graph subgraph + remove_nodes_from` (3.32s,
  the single biggest line here) as the two real costs. Notably, "local
  search radius: 54.7mm, 155816 vertices within range" == the mesh's ENTIRE
  vertex count -- meaning the "restrict flood-fill to a local neighborhood"
  optimization (added during the 8-round Isolate Patch saga) wasn't
  actually restricting anything in this case, so `graph.subgraph(nearby_set)`
  paid the cost of copying the WHOLE graph for no narrowing benefit. Not
  yet fixed -- flagged here as a small follow-up if isolate is still felt
  to be slow after other fixes land, not yet prioritized since 5.7s is
  already a small fraction of the ~45s total.

**Fixed (2026-07-30): `_closest_component_to_point()`'s full-array EDT
fallback -- confirmed real bug, not just theoretical. Took TWO rounds to
get right, and the first round's synthetic test was misleading.** The
12.91s "component selection" line above is `segment_pinna_threshold.
_closest_component_to_point()`: when the ear-center landmark's own voxel
isn't itself part of any labeled component (common -- lands a hair below
threshold), it fell back to `scipy.ndimage.distance_transform_edt(labeled_
array == 0, return_indices=True)` over the WHOLE ~41.5M-voxel array, to
answer a single-point nearest-neighbor query.

Round 1: replaced with a `cKDTree` built from every foreground voxel's own
coordinates. A synthetic test (small, SPARSE foreground -- a few small
blobs in mostly-empty space) showed a 57.7x speedup and looked airtight.
Real-Slicer re-test only showed 12.91s -> 7.30s (~1.8x) -- a real
improvement, but nowhere near the synthetic prediction. Root cause: real
scans are NOT sparse here -- Thomas's log showed ~12.9M of ~41.5M voxels
(31%) as foreground across 22 components. Building a cKDTree over that
many points isn't free either; round 1's synthetic test just didn't match
real foreground density, so it didn't catch this -- another instance of
this project's repeated lesson (see [[feedback_synthetic_tests_limits]]),
this time on a synthetic test that was too EASY rather than one that
missed a real-world failure mode.

Round 2 (tried, REJECTED): a "growing local search box" around the query
point (small EDT on an expanding local crop, on the theory that the
landmark is always close to real tissue). Directly measured to sometimes
be WORSE than the original full-array EDT (10.14s vs. 9.62s baseline, on
a realistic-density synthetic array) when the query point happened to sit
in a gap between components -- the small boxes found nothing, wasting
time before falling back to the full-array approach anyway. Locality is
not guaranteed, so this approach was abandoned rather than shipped.

Round 3 (shipped): the key insight is that for a query point OUTSIDE
every component, the nearest foreground voxel can NEVER be a component's
INTERIOR voxel -- only SURFACE voxels (foreground adjacent to at least
one background voxel, via one `scipy.ndimage.binary_erosion` + XOR) can
ever be the true answer. For solid blob-shaped components, surface voxel
count scales with (volume)^(2/3), not volume -- confirmed via synthetic
test at realistic ~20-30% foreground density (22 components): surface
voxels were only ~7.7% of total foreground voxels. Building the cKDTree
over just those instead: **~10x faster than the original full-array EDT
(12.65s -> 0.82s + 0.44s one-time surface extraction), ~5.5x faster than
round 1's all-foreground cKDTree**, verified identical results across 10
random trials, no locality gamble (unlike round 2, never slower than
baseline in any trial tested). This is what's actually in
`segment_pinna_threshold.py` now.

**CONFIRMED on a real re-test (2026-07-30, third real run)**: `component
selection` dropped **7.30s -> 0.99s**, matching the synthetic prediction
almost exactly. This bug is fully closed out. `segment_pinna_region`
TOTAL dropped 13.52s -> 8.31s; Stage A grand total 37.83s -> 35.22s (a
smaller net drop than component-selection's own savings alone would
suggest, since `smooth_boundary` 18.13s and `label_map_to_mesh` 23.64s
both ran slightly higher this time than in the prior run -- most likely
ordinary system variance between runs, not a regression). The only two
large remaining costs are now clearly `smooth_boundary` and
`label_map_to_mesh`'s marching_cubes, both voxel-array-size-bound (see
the tight-bounding-box crop, shipped immediately after this fix in the
same session -- next paragraph).

**Shipped (2026-07-30): tight-bounding-box crop, the "Next fix on the
table" from before.** `roi_crop.crop_to_own_bounding_box(label_image,
margin_mm)` -- crops a label image down to its OWN foreground's bounding
box (plus `PINNA_TIGHT_CROP_MARGIN_MM` = 15mm, chosen generously to cover
both `smooth_boundary()`'s 1-voxel-per-iteration morphological reach AND
leave the surgeon real headroom to paint missed material back in via
Segment Editor on the same pushed volume -- NOT just a few mm for
processing safety alone). Applied at all 3 pinna call sites, right after
`postprocess.run_full_postprocess()` and before both the Slicer
segmentation-node push and `mesh_export.label_map_to_mesh()` (same 3
sites decimation was added to: `page_pinna_review.py`'s
`_on_run_clicked()`/`_refresh_mesh_from_segmentation()`,
`page_pinna_draw.py`'s `_build_fallback_mesh()`).

**Correctness verified first (per this project's RAS/LPS bug history) --
critical, and confirmed clean**: built a synthetic label image, ran
`mesh_export.label_map_to_mesh()` on both the full array and the
cropped-via-`crop_to_own_bounding_box()` array, and compared vertex
positions in physical (RAS mm) space via nearest-surface distance in
BOTH directions. Result: **0.000000mm difference, in two different
synthetic geometries** (an oversized blob, and a more realistic
hemisphere-shaped one) -- `sitk.RegionOfInterest`'s origin bookkeeping
(used internally) is correctly transparent to marching_cubes' physical
vertex placement. Safe to ship.

**Speedup magnitude -- honest, and smaller than component selection's
fix, verified with repeated trials after a misleading single-run
result**: a first synthetic geometry (an oversized blob nearly filling
its own box) showed almost no reduction -- unrealistic, since real
segmented tissue is a meaningfully smaller fraction of the coarse ROI
crop. A second, more realistic geometry (a hemisphere-shaped blob within
the ROI sphere's own bounding box, modeling that the ear_center landmark
sits roughly ON the skin surface, so the sphere is roughly half air /
half tissue) showed the crop reducing voxel count to 66.6% of the
original (the 15mm margin eats into what would otherwise be closer to
50%). A single marching_cubes trial at that geometry misleadingly showed
NO speedup (0.86x, i.e. slightly slower) -- pure run-to-run timing noise
on this machine (marching_cubes' own timing varied by several seconds
across otherwise-identical real Slicer runs earlier this session too).
Averaging `smooth_boundary` alone over 3 repeats gave a clean signal:
**20.89s -> 14.29s, a real 1.46x speedup**, closely matching the 1/0.666
= 1.50x the voxel-count reduction alone would predict. Lesson: don't
trust a single timing sample near the scale of this system's run-to-run
noise -- average over repeats before concluding an optimization did
nothing.

**Real-Slicer result (2026-07-30, same day): the hemisphere assumption was
WRONG -- negligible real benefit, though still correct/harmless.** Thomas
re-ran the same scan. `crop_to_own_bounding_box` reported (380,436,258) ->
(371,436,258) -- only the X dimension shrank, by 9 voxels (~2.4%); Y and Z
were completely unchanged. `smooth_boundary` (15.65s) and `label_map_to_mesh`
(19.75s) came back essentially identical to before this fix, within the
run-to-run noise band already seen across every prior run. Stage A grand
total: 33.26s (barely different from the post-component-selection-fix
35.22s -- the small further drop is consistent with ordinary noise, not
this crop). **Real pinna anatomy apparently occupies nearly the ENTIRE
ROI sphere's bounding box in every direction**, not roughly half of it as
the hemisphere synthetic test assumed -- likely because the pinna's own
curls (helix etc.) reach close to the sphere's radius in most directions,
unlike a simple flat "skin surface cutting the sphere in half" model.
This crop is left in (it's provably correct and can only ever help, never
hurt, on some other scan/landmark placement where tissue happens to be
less space-filling), but it should NOT be counted on as a real fix for
this specific bottleneck going forward.

**Where this leaves Stage A**: `smooth_boundary` (~15-18s) and
`label_map_to_mesh`'s marching_cubes (~20-24s) are now the only two large
costs left, and every zero-quality-cost lever that could shrink the voxel
count they operate on has been tried (roi_crop's own grid-building fix,
component selection, this tight crop). Further speedup here would require
an actual quality/robustness tradeoff, not a free win:
- Lowering `PINNA_ROI_RADIUS_MM` -- functional risk (could clip real
  pinna anatomy for some patients), a clinical judgment call for Thomas,
  not a code change to make unprompted.
- Reducing `smooth_boundary`'s iteration count (currently 2) -- a genuine
  smoothing-quality tradeoff, however small.
- `marching_cubes`' own `step_size` -- Thomas already explicitly declined
  this earlier in favor of quality-preserving decimation, for the same
  reason it would apply here too.
Given all three require an explicit tradeoff, none should be pursued
without asking Thomas first. Stage A is now ~33s (down from the original
45.41s at the start of this session, ~27% overall, entirely from the
component-selection bug fix -- this crop's contribution turned out to be
negligible on real anatomy).

**UI responsiveness fix (2026-07-30, later same day)**: Thomas reported
that even with the timing above being "not too bad," 3D Slicer visibly
goes unresponsive ("Not Responding" title bar on Windows) during these
multi-second single calls -- a real UX problem distinct from raw speed,
since a surgeon unfamiliar with what's happening could easily mistake
this for an actual crash. Root cause: every heavy call
(`build_spherical_roi_mask`, `segment_pinna_region`,
`postprocess.run_full_postprocess`, `crop_to_own_bounding_box`,
`mesh_export.label_map_to_mesh`, `decimate_to_target_resolution`) was
called directly on Slicer's main/UI thread, blocking its Qt event loop
(no window-message pumping) for the call's entire multi-second duration --
`slicer.app.processEvents()` can only be called BETWEEN separate calls,
never DURING one opaque C call, so the existing between-steps
`processEvents()` calls never helped for the biggest individual offenders.

**Fix: `base_page.WizardPage.run_blocking()`** (new shared helper) runs a
given zero-argument callable on a background Python `threading.Thread`
while the calling (main) thread polls `thread.is_alive()` and calls
`slicer.app.processEvents()` in a tight loop until it finishes -- keeping
Slicer's window message pump alive and the UI visibly responsive for the
computation's whole real duration (this does NOT make the computation
itself any faster, only keeps the UI alive while it runs). Safe
specifically because every `core/` module in this project is already
Slicer/VTK-independent by design (operates only on
`sitk.Image`/`numpy`/`trimesh` objects) -- the background thread never
touches the MRML scene or a Qt widget, so there's no non-main-thread
VTK/Qt safety concern; the only Slicer API call happens on the main
thread, exactly as it always should. Exceptions raised inside the
background callable are re-raised on the caller's thread automatically.

Wired into BOTH places in `page_pinna_review.py` that run this same
heavy postprocess+meshing sequence: `_on_run_clicked()`'s main pipeline
(refactored into a new `_run_segmentation_pipeline()` helper method for
clarity) and `_refresh_mesh_from_segmentation()` (runs on "Next" if the
surgeon used Segment Editor -- same heavy calls, same risk). A new
`progressBar` widget (added to `page_pinna_review.ui`, hidden by default)
advances one tick per named pipeline stage (7 stages in the main run, 4
in the edited-segmentation refresh) with a matching `statusLabel` message
for each -- coarse, not fine-grained within a stage, but enough to give
real confirmation that a specific, currently-running step is still
working rather than one long silent wait. `runButton` is disabled for
the pipeline's duration to prevent a double-click starting a second
concurrent run.

**Verified with the actual shipped code (scratchpad, not committed)**:
faked `slicer.app.processEvents()` as a counter and called the real
`run_blocking()` directly -- confirmed (1) correct return value and
correct total elapsed time for a slow call, (2) `processEvents()` called
repeatedly (18 times) DURING a 1-second call, proving the main thread
isn't blocked for the call's duration (the actual bug being fixed), (3)
an exception raised inside the background callable correctly propagates
back out to the caller, and (4) genuine concurrency (a separate counter
thread kept incrementing throughout a `run_blocking()` call, confirming
real parallelism under Python's GIL for this pattern, not just
cooperative multitasking). **Not yet confirmed in real Slicer** -- next
session should ask Thomas whether the "Not Responding" title bar still
appears at all during a real run, and whether the progress bar/status
text updates are visible and feel responsive in practice.

**Extended to pinna isolation and canal segmentation (2026-07-30, same
day, Thomas liked the result and asked for the same treatment
elsewhere)**:

- **`page_pinna_draw.py`'s `_on_isolate_clicked()`** (the Isolate Patch
  button): refactored into `_run_isolate_pipeline()` following the same
  pattern, with a new `_advance_progress(label)` helper tracking a plain
  Python `self._progress_step`/`self._progress_max` pair (NOT read back
  from the Qt widget -- this codebase has no prior example of reading a
  QProgressBar property back via PythonQt, so tracking it in Python
  avoids relying on an unconfirmed API). This page's total step count
  isn't knowable upfront: the common path is 3 steps (load mesh, crop
  toward canal, isolate), but the `LoopDoesNotSeparateError` fallback
  retry path (see the 8-round Isolate Patch saga) adds up to 5 more (the
  fallback rebuild's own postprocess/crop/mesh/decimate steps, plus a
  second crop+isolate attempt) -- `_advance_progress()` bumps the bar's
  max on the fly if the step count exceeds what was originally set,
  rather than requiring it known in advance. `_build_fallback_mesh()`
  (only reached from the failure path) now also runs its own internal
  heavy calls through `run_blocking()`, since real-Slicer logs showed
  this retry path alone can cost ~30+ seconds (a full postprocess+remesh
  round trip) -- exactly the situation this fix targets.
- **`page_scutum_review.py`'s `_on_run_clicked()`/`_refresh_mesh_from_segmentation()`**:
  same pattern as the pinna review page (refactored into
  `_run_segmentation_pipeline()`), covering `build_roi_mask`,
  `crop_to_roi_bounding_box`, `segment_dl.segment()` (can be ~7x slower
  than plain thresholding with `ENABLE_SHEET_ENHANCEMENT` on, see
  "Improving Canal Segmentation" below), `postprocess.run_full_postprocess()`,
  and `mesh_export.label_map_to_mesh_subvoxel()`/`label_map_to_mesh()`.
  Canal segmentation is faster than pinna's on a typical scan (Thomas's
  own report: "canal segmentation steps all run at a pretty usual pace"),
  so this is more a consistency/reassurance measure than a fix for an
  already-reported problem there -- applied for the same reason pinna
  needed it (a slower scan or machine could still hit the same
  single-call multi-second block), not because canal was itself flagged
  as freezing.

All three pages now share the exact same `run_blocking()` helper from
`base_page.py` -- no new threading logic was written, just wired into two
more call sites. New `progressBar` widgets added to `page_pinna_draw.ui`
and `page_scutum_review.ui` (page_pinna_review.ui already had one). Not
yet real-Slicer tested for these two pages either.

**Curvature page (2026-07-30, same day): a real progress bar, not
background-threading.** page_curvature.py is architecturally different
from the other three -- it already had its own progress-reporting
mechanism (`curvature_integration.run_curvature_comparison(...,
progress_callback=self._on_progress_line)`, wired through to
`core/curvature/pipeline.py`'s `progress(...)` calls at named steps and
periodically during its two counted loops: candidate scoring, up to
`CURVATURE_NUM_CANDIDATES`=300, and top-candidate ICP refinement, up to
`CURVATURE_TOP_N_REFINE`=15). Deliberately did NOT wrap this in
`run_blocking()`: that would require the progress callback (which
currently touches Qt directly -- `self.ui.statusLabel.setText()` and
`slicer.app.processEvents()`) to run safely from a background thread,
which it doesn't do today, and retrofitting that is a bigger, riskier
change than what was actually asked for ("a bar in place of where the
python line is").

Instead, added a `progressBar` widget (page_curvature.ui, positioned
right above `statusLabel`, hidden except during a run) and taught
`_on_progress_line()` (page_curvature.py) to parse the *existing*
progress strings via two regexes (`_PHASE_TOTAL_RE`/`_PHASE_PROGRESS_RE`)
matching the pipeline's own known message formats ("Scoring 300
candidates...", "  scored 30/300 candidates", "Refining top 15
candidates...", "  [3/15] vertex ...") -- no changes needed to
`core/curvature/pipeline.py`'s interface, preserving that module's
deliberate "no Slicer/Qt dependency at all" design. Any line matching a
phase-total pattern resets the bar to a fresh determinate 0..N range;
any line matching a progress pattern sets the current value; any other
line (e.g. "Loading meshes...", "Done.") falls back to Qt's
indeterminate/"busy" idiom (`setMinimum(0)`/`setMaximum(0)`) so the bar
still visibly animates during named steps with no known fraction rather
than sitting frozen at wherever the last phase left it. Verified directly
(scratchpad, not committed) against every actual message string
`pipeline.py`'s `run()` emits -- both counted-phase message shapes parse
correctly, every other named-step message correctly falls through to
indeterminate.

Also tightened the candidate-scoring loop's own reporting interval from
every 25 candidates to every 10 (`core/curvature/pipeline.py`) -- a
smaller, in-scope adjustment: a coarser interval left long real gaps with
NO `progress_callback`/`processEvents()` call at all between ticks
(25 candidates' worth of geodesic patch extraction + scoring, each
individually nontrivial), the same underlying "Not Responding" risk as
the other three pages' single long C calls, just manifesting through a
periodic-callback loop instead. This doesn't eliminate that risk (still
no background thread here), but shortens the worst-case gap between UI
updates by more than half.

Not yet real-Slicer tested.

---

## Pinna mesh decimation topology bug (2026-08-22)

**Status: fix shipped, synthetically confirmed, NOT yet real-Slicer
tested -- ask Thomas to re-run the same scan/threshold he reported this
on and confirm before considering this closed.**

Thomas reported (after a break, first session back) that pinna
segmentation was giving "much less accurate" results than before, on the
*same scan and same threshold value* -- described specifically as: the
basic pinna shape is present, but low resolution, with many holes/chunks
missing (not spiky). He'd tried both setting the threshold via the
calibration points and typing/dragging the slider directly and got
similar bad results either way -- a strong signal the bug was downstream
of thresholding entirely, not a calibration/threshold issue.

**Root cause, confirmed via synthetic test (scratchpad, not committed) --
not a guess:** `mesh_export.decimate_to_target_resolution()` (shipped
2026-07-30, see "Pinna segmentation performance" above) calls
`trimesh.simplify_quadric_decimation()` (backed by `fast_simplification`)
with no repair pass afterward. That function's original validation only
checked nearest-surface DISTANCE accuracy on a simple rounded blob shape
-- never watertightness, and never a shape with a thin curled fold like
the pinna's own helix. Built a synthetic "pinna-like" label volume (a
main lobe + a thin curled ridge ~4 voxels thick, mimicking a helix fold,
at fine native spacing matching real scans Thomas has reported) and ran
the actual shipped code path: the mesh coming OUT of marching_cubes was
clean (watertight=True, 2 sensible connected components: lobe + ridge).
After `decimate_to_target_resolution()`, the SAME mesh came out
`is_watertight=False`, fragmented into 5 components -- the 2 real ones
plus 3 new degenerate 3-vertex slivers carved off the thin ridge by the
decimation algorithm. A sliver separating from the surface leaves a real
gap behind -- exactly Thomas's "holes/chunks missing" description, and
it happens regardless of the threshold value used, matching his own
observation.

**Fix**: new `mesh_export._repair_decimation_fragments()`, called at the
end of `decimate_to_target_resolution()` -- splits the decimated mesh
into connected components, drops any component smaller than
`config.DECIMATION_FRAGMENT_MIN_TRIANGLES` (20) "target-sized" triangles
(relative to `PINNA_MESH_TARGET_EDGE_MM`, not a fixed absolute vertex/
area count, so it scales with target density instead of needing
re-tuning per scan resolution -- same lesson this project already
learned once with `SHEET_ENHANCEMENT_SCALE_MULTIPLIERS`), then runs
`trimesh.repair.fill_holes()`. Safe to drop these fragments specifically
because the mesh going INTO decimation is already clean --
`postprocess.remove_small_specks()` already removed any genuinely small
segmented tissue in voxel space, well before meshing -- so anything this
small appearing only AFTER decimation is provably decimation debris, not
real anatomy that slipped through.

**Re-validated on the real shipped function** (not just the standalone
scratchpad copy) on two geometries -- the original 4-voxel-thick ridge
and a much thinner, harder 2-voxel-thick ridge: both came back
`is_watertight=True` post-decimation with the same euler number as
before decimation, and the ridge component itself preserved (thousands
of vertices, not dropped) in both cases -- not just the garbage
fragments.

**Not yet real-Slicer tested** -- per this project's own repeated lesson
([[feedback_synthetic_tests_limits]]), a synthetic topology test can
confirm the mechanism and that the fix doesn't regress a clean case, but
only Thomas re-running the actual scan he reported this on can confirm
it's actually fixed in practice. Next session: ask him to re-run pinna
segmentation on that same scan/threshold and check whether the
holes/missing chunks are gone and resolution looks normal again.

**First real-Slicer attempt (2026-08-22, same day): "looks exactly the
same" -- not yet explained.** Thomas re-ran the scan and reported no
visible change at all. Since a fix that changes nothing visually usually
means it isn't actually running, added TEMPORARY `[mesh_export diag]` /
`[pinna diag]` prints (see `core/mesh_export.py`: one at module-import
time confirming `__file__`, three per call inside
`decimate_to_target_resolution()`/`_repair_decimation_fragments()`
showing real vert/face/watertight/component counts at each stage) --
same stale-code-suspicion pattern as the sheetness feature's "is the code
even being applied?" moment. **Not yet checked** -- next step is asking
Thomas to fully restart Slicer, re-run the same scan, and paste the
console output. All 3 call sites
(`page_pinna_review.py` Run + Segment-Editor-refresh,
`page_pinna_draw.py`'s fallback mesh) confirmed still wired to call this
exact function, so the diagnostic will fire regardless of which path he
used. Remove these prints (and the temporarily-forced-visible
`restartSlicerButton` below) once this is resolved either way.

**`page_setup.py`'s `restartSlicerButton` temporarily forced visible
(2026-08-22)**: normally auto-hidden once `dependencies.
check_missing_packages()` finds nothing missing (see "Restart Slicer
button" under "Wizard/Slicer-specific notes" below for why it's hidden by
default) -- Thomas asked for it back purely to speed up the repeated
restart-and-retest cycle this diagnostic requires. `_refresh_status()`'s
`if not missing:` branch now hard-codes `setVisible(True)` instead of
`False`, clearly commented as temporary. **Revert this (restore the
`False`) once the diagnostic-print testing above wraps up** -- it's not a
bug fix, just a convenience for this specific testing session, and the
original hidden-when-nothing-missing behavior is still the right default
for a returning surgeon.

**Real console output came back (2026-08-22, same day): decimation
confirmed NOT the culprit on this scan -- the mesh was already broken
before decimation ever ran.** Thomas's `[pinna diag]`/`[pinna timing]`
output showed `decimate_to_target_resolution() called: ... watertight=False
components=2` immediately followed by `no-op (mesh already coarser than
target)` -- i.e. the fix shipped above never even executes on this scan,
because `label_map_to_mesh()`'s own output (63,727 verts) is already at
or below `PINNA_MESH_TARGET_EDGE_MM`'s target density. The non-watertight,
2-component mesh is present BEFORE decimation touches it, so the fix,
while a real and independently-confirmed bug (see above), is not what
Thomas is seeing when he says "looks exactly the same."

**New, stronger lead from the same console output**: this scan's spacing
is `(0.390625, 0.390625, 2.5)mm` -- only 32 axial slices, 2.5mm apart.
Every previously-tested real scan in this project's history was
near-isotropic (0.173-0.5mm on every axis); this is the first real run on
a scan this coarse/anisotropic in Z. Prime suspect: something in
`segment_pinna_threshold.segment_pinna_region()` (component selection +
`_remove_boundary_spike`) or `postprocess.run_full_postprocess()`
(`remove_small_specks`/`fill_holes`/`smooth_boundary`) is turning a
single selected component into 2 disconnected pieces somewhere along the
way -- by construction, `segment_pinna_region()`'s own component
selection (`region_mask_array = (labeled_array == best_component_id)`)
and `_remove_boundary_spike()`'s post-opening re-selection both guarantee
a single component going OUT of that function, so if the count is already
>1 there, something is genuinely surprising; more likely it's introduced
in `run_full_postprocess()`.

**One specific theory tested and REJECTED, don't re-suggest**:
suspected `postprocess.smooth_boundary()`'s fixed `[1,1,1]`-voxel
`BinaryMorphologicalClosing`/`Opening` radius would behave far more
aggressively along Z on this scan (a "1 voxel" radius = ~2.5mm of real
physical reach in Z vs ~0.39mm in X/Y), possibly severing a thin
anatomical bridge that's only 1-2 voxels thick in Z. Directly tested
(scratchpad, not committed): built a synthetic two-lobe-plus-thin-bridge
geometry at both this scan's real anisotropic spacing and an isotropic
control, ran the actual `smooth_boundary()` on both. **Identical results
in both cases, bridge survived intact in both** -- confirmed
`sitk.BinaryMorphologicalClosing`/`Opening`'s radius parameter is defined
in voxel counts, not physical mm, and is completely blind to
`Image.GetSpacing()`. This specific mechanism doesn't apply; the real
cause is still unknown.

**Diagnostics added to find it for real (2026-08-22, TEMPORARY, remove
once found)**: `page_pinna_review.py` now has a `_diag_component_count()`
helper (voxel-space, 6-connectivity, same convention as
`segment_pinna_threshold._label_6_connected`) printing `[pinna diag]
component count after <stage>` at 3 checkpoints inside
`_run_segmentation_pipeline()`: right after `segment_pinna_region` (which
includes spike removal), right after `run_full_postprocess`, and right
after `crop_to_own_bounding_box`. **Next session: ask Thomas to re-run
the same scan and paste the console output** -- whichever checkpoint
first shows >1 components pinpoints the culprit directly, rather than
guessing again. If all three checkpoints show 1 (i.e. the voxel data
stays a single connected blob the whole way through Stage A), the bug
isn't in voxel-space postprocessing at all -- it would have to be in
`mesh_export.label_map_to_mesh()`'s own marching_cubes + `trimesh.repair.
fill_holes(mesh)` step, meaning marching_cubes on this scan's extreme
Z-anisotropy is producing a topologically split mesh from a genuinely
single-component voxel mask -- a different class of bug from anything
checked so far, worth testing synthetically (a solid blob at this same
0.39/0.39/2.5mm spacing run straight through `label_map_to_mesh()`)
before touching any code.

**Real console output confirmed exactly this (2026-08-22, same day):**
Thomas's actual `[pinna diag] component count after ...` lines all read
`1` (segment_pinna_region incl. spike removal, postprocess, tight-crop),
then `decimate_to_target_resolution() called: ... watertight=False
components=2` -- the input mesh to decimation was ALREADY broken, meaning
`label_map_to_mesh()` itself is where a genuinely single-component voxel
mask becomes a torn, 2-component mesh. **Second real data point, strongly
corroborating**: Thomas separately tried a different scan at 0.43/0.43/
0.5mm spacing (~1.16:1 Z:XY ratio -- near isotropic) and got normal,
accurate results resembling prior good segmentations. The broken scan is
0.39/0.39/2.5mm (~6.4:1 ratio). This is a clean, well-known failure mode:
marching-cubes-family algorithms can produce topologically inconsistent
surfaces on severely anisotropic grids. Every scan this pipeline had been
tested on before this session was near-isotropic (0.173-0.5mm on every
axis) -- this is the first real run on a scan this anisotropic in Z,
which is why it never surfaced earlier.

**Fix shipped**: new `max_anisotropy_ratio` parameter on `mesh_export.
label_map_to_mesh()` -- when the label image's coarsest-axis spacing
exceeds `max_anisotropy_ratio` times its finest-axis spacing, upsamples
the coarse axis (linear interpolation on the mask cast to float,
re-thresholded at 0.5 -- the standard sub-voxel-accurate way to resample
a binary mask, same technique `mask_blur_sigma_mm` already uses for a
different purpose) via the new `_resample_to_bounded_anisotropy()`
helper, BEFORE marching_cubes ever sees it. Wired into all 3 pinna call
sites (`page_pinna_review.py`'s Run + Segment-Editor-refresh,
`page_pinna_draw.py`'s fallback mesh) via new `config.
MESH_MAX_ANISOTROPY_RATIO = 2.0` (a starting value: comfortably below the
~6.4:1 ratio that broke, comfortably above the ~1.16:1 ratio that
worked -- not yet tuned against more real data points either way). Default
parameter value is `0.0` (disabled), so every other caller (scutum) is
completely unaffected.

**Synthetic reproduction of the actual tear did NOT succeed** -- two
attempts at building a thin curled/ribbon shape meant to mimic a real
helix fold both had construction bugs (the "ridge" and "main lobe" ended
up NOT touching in voxel space to begin with, an artifact of the test
geometry, not a real finding) rather than reproducing marching_cubes
tearing a genuinely-connected mask. Not worth further blind attempts
right now given how strong the real-scan evidence already is (2 real
data points, clean correlation with anisotropy ratio, exact mechanism
matches a well-documented general failure mode). **What WAS validated
synthetically**: the fix is safe -- no-ops correctly on near-isotropic
spacing (confirmed at the same 0.43/0.43/0.5mm ratio as Thomas's working
scan), doesn't regress a case that already meshed fine (a solid blob at
the broken scan's exact 0.39/0.39/2.5mm spacing, watertight before and
after), and when it does resample, volume changes by only 0.35% --
negligible shape distortion.

**Not yet confirmed on the actual failing scan** -- the fix is
well-motivated and safe, but nobody has run it against Thomas's real
broken scan yet, since the specific tear couldn't be reproduced
synthetically to test against directly. Next session: ask Thomas to
restart Slicer and re-run the SAME scan that showed `watertight=False
components=2`. Watch for a new `[pinna diag] _resample_to_bounded_
anisotropy: spacing ... -> ...` line (confirms it fired) and check
whether `decimate_to_target_resolution() called: ...` now reports
`watertight=True components=1` (or however many real anatomical pieces
there should be) -- and, most importantly, whether the visible mesh
actually looks right again in Slicer. If it's still broken, the ratio
(2.0) is the first knob to try lowering (forces earlier/more aggressive
resampling); if that still doesn't help, the tear may not be a pure
anisotropy effect and needs the per-component breakdown of the broken
mesh (vertex counts, individual watertight status per component) to
understand what's actually happening -- not yet added as a diagnostic,
would be the next thing to add if this fix doesn't resolve it.

**Real re-test (2026-08-25): the ratio-2.0 mask-level fix fired, but
still wasn't enough -- per-component breakdown revealed why.** Thomas
re-ran the same scan. The resample DID trigger (`spacing (0.390625,
0.390625, 2.4999677419354835) -> (0.390625, 0.390625, 0.78125)`), but
`decimate_to_target_resolution() called: ...` still reported
`watertight=False components=2`. Following this section's own
established practice (diagnose before guessing again), added a
per-component breakdown to that same diagnostic (vertex/face count,
area, watertight status, bounds for each component when count > 1)
instead of immediately trying another fix. The real numbers: component 0
(the main lobe) was 112249 verts, 19142.71mm², **itself
`watertight=False`** (i.e. it has a genuine hole, not just a separate
neighbor); component 1 was 188 verts, 18.01mm², fully `watertight=True`
on its own -- a real, physically plausible small piece of anatomy (too
large to be a decimation-debris sliver, unlike the earlier,
unrelated decimation-fragment bug above), not something safe to just
drop. This ruled out reusing `_repair_decimation_fragments`-style
drop-small-fragments logic here -- it would risk deleting real tissue.

**Root cause pinned down by Thomas's own direct experiment**: he
manually resampled the whole scan to isotropic spacing *before* running
the pipeline (not just the mask right before meshing) and got a
correctly-shaped result. This proved the fix needed to move much earlier
in the pipeline -- `segment_pinna_region`'s thresholding/connected-
components/spike-removal (`_remove_boundary_spike`'s morphological
opening) and `postprocess.run_full_postprocess()`'s own morphology
(`remove_small_specks`/`fill_holes`/`smooth_boundary`, all voxel-radius-
based per this section's own earlier-confirmed finding that these
operations are blind to physical spacing) were all still running on the
raw 32-slice anisotropic grid before the late, mesh-only fix ever got a
chance to help -- by the time marching_cubes saw the data, a real fold
may already have been mis-processed by earlier steps operating on
badly-conditioned voxels.

**Fix shipped (2026-08-25), two parts:**
1. **Made the resample itself more aggressive.** `_resample_to_bounded_
   anisotropy`'s target spacing changed from "resample only down to the
   trigger ratio" (`min_spacing * max_ratio`) to "resample all the way
   to match the finest axis" (full isotropy) once triggered --
   `MESH_MAX_ANISOTROPY_RATIO = 2.0` remains only the TRIGGER, not the
   target, so already-near-isotropic scans are still completely
   unaffected. On this scan, Z now goes to 0.390625mm (matching X/Y)
   instead of stopping at 0.78125mm.
2. **Moved the resample far earlier in the pipeline, and generalized it
   to grayscale data.** The logic was extracted out of `mesh_export.py`
   (where `_resample_to_bounded_anisotropy` was mask-only, threshold-
   based) into a new shared `io_utils.resample_to_bounded_anisotropy
   (image, max_ratio, is_label=False)` -- same trigger/target math, plus
   an `is_label` flag: `True` casts-to-float/resamples/re-thresholds at
   0.5 (for a binary mask, unchanged behavior), `False` does plain linear
   interpolation on grayscale intensity data, cast back to the original
   pixel type. `mesh_export.label_map_to_mesh()`'s own late-stage call
   (right before `marching_cubes`) now just delegates to this shared
   function (`is_label=True`) rather than duplicating the logic, and
   stays in place as defense-in-depth (now usually a no-op, since the
   volume is already isotropic by the time it gets there). The real fix
   is 3 NEW call sites, each as early as possible in its own flow:
   - `page_pinna_review.py`'s `_run_segmentation_pipeline()` -- resamples
     the grayscale `coarse_cropped` volume right after the coarse crop,
     BEFORE `build_spherical_roi_mask()`/thresholding/spike-removal/
     postprocess ever run.
   - `page_pinna_review.py`'s `_refresh_mesh_from_segmentation()`
     (Segment-Editor-edited path) -- resamples the binary labelmap right
     after pulling it from the segmentation node, before
     `postprocess.run_full_postprocess()`.
   - `page_pinna_draw.py`'s `_build_fallback_mesh()` (Isolate Patch
     `LoopDoesNotSeparateError` retry path) -- same treatment as the
     refresh path above.

   Deliberately did NOT adopt the existing-but-unused `io_utils.
   resample_to_isotropic()` (always resamples every scan to one fixed
   absolute spacing, `TARGET_VOXEL_SPACING_MM`) -- Thomas specifically
   recalled an earlier version of this program doing exactly that and
   getting WORSE results, consistent with this project's own repeated
   "implicit fixed working-resolution assumption never actually true on
   real data" lesson (see the sheetness feature's multi-round scale-
   calibration saga below). The shipped fix stays scan-relative
   (only intervenes when a scan is ALREADY badly anisotropic) rather than
   imposing one resolution on every scan.

**First real-Slicer re-test (2026-08-25, same day): "much better."**
Thomas re-ran the same previously-broken scan and confirmed a clear
improvement -- no longer the blocky/missing-chunks look. **Provisional,
not fully closed**: he wants to test more scans before calling this
fully resolved, so the TEMPORARY diagnostic prints (`[mesh_export diag]`,
`[pinna diag]` component-count checks and the resample helper's own
print, now also present in `io_utils.py`) and the forced-visible
`restartSlicerButton` should all stay in place until that broader
validation completes -- see "Known open issues" item 6.

---

## Interactive Segment Editor threshold rework (2026-07-30)

Large architecture change to `page_scutum_review.py`, replacing the custom
SimpleITK threshold pipeline (two plain sliders -> `segment_dl.segment()` ->
`segment_threshold.segment_bone_wall()`, with a separate "Open Segment
Editor" button that navigated away to Slicer's SegmentEditor module for
manual touch-up) with a real `qMRMLSegmentEditorWidget` embedded directly
in the page.

**Motivation**: after the multi-round sheetness/CurvatureFlow struggle
documented in "Improving Canal Segmentation" below (four real-scan A/B
rounds, none of which fixed the thin-wall/scutum-malleus-fusion
complaints), Thomas reported that Slicer's own interactive Threshold
effect -- live preview as you drag, directly against the real data --
was consistently more accurate than the automated pipeline could manage.
Rather than keep tuning constants against a low-resolution generalization
problem no amount of calibration seemed to solve (see that section's
closing "Lesson"), this rework puts a human in the loop for the actual
threshold decision, using Slicer's own mature, well-tested tool instead
of reimplementing it.

**New flow**:
1. **"Auto-Calibrate & Segment" (one click)**: computes a starting bone
   threshold -- from the existing optional seed calibration
   (`core/threshold_seeds.py` -- a bone point placed here, combined with a
   soft-tissue point placed earlier on the pinna review page; see
   "Calibration-seed cleanup" below for the current, reworked shape of
   this, last touched 2026-07-31) if placed, else
   `config.DEFAULT_BONE_THRESHOLD` -- and applies it via the Threshold
   effect (`effect.setParameter
   ("MinimumThreshold"/"MaximumThreshold", ...)`, `effect.self().onApply()`
   -- the documented Slicer script-repository recipe) to the **whole
   loaded volume**, not a shell/ROI restriction. This directly matches
   Thomas's own proposed design ("segment the whole volume, then crop
   afterward using the ROI") -- Threshold is fast native VTK/ITK code even
   over a full CT, unlike the SimpleITK/scipy pipeline steps that needed
   `run_blocking()`'s background-thread treatment (see "Pinna segmentation
   performance" above). A visible segmentation node auto-generates a
   real-time 3D closed-surface representation in Slicer, so the surgeon
   sees a live 3D result immediately -- no custom mesh pipeline needed
   just to preview.
2. **Live manual adjustment**: the embedded widget stays interactive
   afterward. Dragging the Threshold effect's own Minimum/Maximum sliders
   re-previews instantly against the real volume (Slicer's own built-in
   behavior, not custom code). A curated effect list (`setEffectNameOrder
   (["Threshold", "Paint", "Erase", "Islands", "Smoothing"])`,
   `unorderedEffectsVisible = False`) keeps the toolbar simple rather than
   exposing Segment Editor's full effect set, matching this project's
   "sliders not raw thresholds" usability priority -- Paint/Erase/Islands/
   Smoothing cover manual touch-up inline, replacing the old separate
   "Open Segment Editor" button/page-navigation entirely.
3. **"Preview 3D Result" (or Next)**: finalizes -- pulls the segmentation
   node's current content (`ExportVisibleSegmentsToLabelmapNode`), coarse-
   crops to the landmark region, builds the precise cylinder ROI
   (`core/roi_crop.py`, unchanged), and keeps only the connected component
   nearest the canal axis (`segment_threshold._closest_component_to_axis_line`
   / `_label_6_connected`, reused as-is -- the function was already generic
   despite being written for the air-lumen case). This step exists because
   whole-volume thresholding can pick up unrelated bone elsewhere in the
   ROI (ossicles, a sliver of adjacent skull) now that there's no shell
   restriction. Then the existing `postprocess.run_full_postprocess(...,
   close_tunnels=True)` and a mesh export, same as before.

**Known tradeoffs, deliberate**:
- **Nearest-component selection can discard a disconnected manual paint
  addition.** If a surgeon paints material that isn't touching the main
  wall, finalize keeps only the component nearest the canal axis and
  drops the rest. Matches this pipeline's existing shell-restriction
  philosophy elsewhere (prevents accidentally keeping unrelated bone) --
  the Islands effect (in the curated list) is the tool for managing a
  genuinely separate painted region before finalizing.
- **Sub-voxel mesh extraction (`mesh_export.label_map_to_mesh_subvoxel`)
  is no longer used here.** It only makes sense when a mask comes from a
  single known threshold value with nothing else touching it -- once
  Paint/Erase edits can sit on top of the initial threshold, there's no
  single isovalue left to extract against. Falls back to plain
  `label_map_to_mesh()`, same as this page already did for a hand-edited
  segmentation before this rework. A real (if modest, per that feature's
  own original ~15-20% RMS boundary-error claim) precision loss, accepted
  in exchange for the interactive-threshold accuracy gain motivating this
  whole change.
- **`segment_dl.py`/`segment_threshold.py` (including sheetness/
  CurvatureFlow) are no longer called by this page**, but were
  deliberately left in place, not deleted -- `segment_dl.py`'s Stage
  A/Stage B fallback structure remains valid architecture for a
  hypothetical future trained model, and nothing else in the codebase
  currently depends on it being wired into the UI. If Stage B ever gets
  trained, it will need a new call site (this page no longer has one).
- **Downstream-invalidation timing changed**: the old page cleared
  downstream state (drawn outline, verify, heatmap) on every "Run" click.
  The new page has no equivalent single "run" moment (editing happens
  continuously via the live widget), so it instead clears downstream
  state at the start of every **finalize** (Preview 3D Result / Next), and
  tracks whether a finalize is actually needed via a `ModifiedEvent`
  observer on the segmentation node (`_needs_finalize`, set True on any
  Threshold/Paint/Erase/Islands/Smoothing change) so clicking Next twice
  in a row without touching anything in between doesn't needlessly
  re-run the pipeline. This is NOT the same risk pattern as the Isolate
  Patch saga's "unconditional reprocessing eroded an already-good mesh"
  bug (see "Key lessons" below) -- finalize always derives fresh from the
  live segmentation node (the actual source of truth), never re-processes
  an already-finalized mesh.

**First real-Slicer test (2026-07-30, same day): core operations
confirmed good.** Thomas reported the segmentation this produces is
noticeably more accurate than the old automated threshold pipeline, and
that the automation level (one-click calibrate, then live adjust) is
where he wants it -- i.e. the central premise of this rework (a human in
the loop, using Slicer's own mature Threshold effect, beats fighting the
generalization problem sheetness/CurvatureFlow tuning kept hitting) is
validated on a real scan, not just synthetically/by design intent. This
also serves as the first real confirmation that the previously-unconfirmed
API surface actually works: embedding `slicer.qMRMLSegmentEditorWidget()`
in this page's own `.ui` layout, `setEffectNameOrder`/
`unorderedEffectsVisible`, and driving the Threshold effect via
`effect.setParameter(...)`/`effect.self().onApply()` all function in
practice, not just per-docs.

**Still open: unspecified interface fixes.** Thomas said "there are some
fixes to make for the interface" before signing off for the day, without
saying what -- **next session must ask what specifically, before touching
this page again.** Don't guess at UI complaints (button placement, sizing
of the embedded widget's minimum-height=450 frame, wording, the
calibration-seed workflow feeling redundant now that Auto-Calibrate
exists, progress bar behavior, something about the curated effect list,
etc. are all plausible but unconfirmed candidates -- get the actual list
from Thomas first).

**Calibration-seed cleanup (2026-07-31)** -- prompted by Thomas asking
whether all 3 calibration points were still needed post-rework, not by
the "unspecified interface fixes" comment above, though it may turn out
to be the same complaint (that comment had already flagged "the
calibration-seed workflow feeling redundant now that Auto-Calibrate
exists" as one guess -- still don't assume that's confirmed, ask Thomas
directly next time this page comes up).

Checked each of the 3 points (air/bone/soft-tissue) against what's
actually used post-rework: `_on_calibrate_clicked` does
`_, bone_threshold = threshold_seeds.calibrate_thresholds(...)` --
`air_threshold` was computed and silently discarded every time, because
the old shell-restricted pipeline that needed it
(`segment_threshold.segment_bone_wall()`) is no longer called by this
page (see "Interactive Segment Editor threshold rework" above). **Deleted
`air_seed` entirely** -- dead weight, zero behavior change (it was never
used).

Separately, Thomas asked whether a calibration point could help the pinna
review page too (which has never had one) -- and if so, whether the
soft-tissue point could be placed there once and reused, instead of
asking twice. Judged genuinely useful, not just for symmetry: pinna's
`SKIN_AIR_THRESHOLD` (-300) has a large built-in margin against typical
scanner noise (config.py's own comment says as much), but
`SKIN_THRESHOLD_ADJUST_RANGE`'s existing comment already documents a real
scan whose intensity values needed a much lower threshold than the
standard-HU default assumed -- i.e. a genuinely *shifted* scan
calibration, not just noise, which a fixed margin doesn't protect against
regardless of its size. This is also a plausible (unconfirmed) partial
explanation for Known Issue #7's EmptySegmentationError reports, which
were previously attributed only to a mis-landed ear_center landmark.

**Shipped**: `pinna_soft_tissue_seed` moved to a new optional calibration
step on the PINNA REVIEW page (page 4) -- one click on ordinary soft
tissue near the ear, placed via the same fiducial-node/Place-mode pattern
already used elsewhere. Placing it immediately pre-fills that page's own
`skinThresholdSlider` via a new `core/threshold_seeds.
calibrate_skin_threshold()`: `soft_tissue_hu - config.
SKIN_THRESHOLD_OFFSET_BELOW_SOFT_TISSUE_HU` (300.0 -- picked so a
*typical* soft-tissue reading lands close to the old fixed -300 default,
while a *shifted* scan's threshold moves with it). Deliberately NOT a
midpoint against a second air-seed click: true open air sits at a
scanner-invariant ~-1000 HU by definition of the Hounsfield scale, so a
fixed offset below the (per-scan-sampled) soft-tissue reading already
tracks a scan-wide shift the same way `calibrate_bone_threshold`'s
midpoint does, without a second click. Smooths with plain Gaussian at
`GAUSSIAN_SMOOTHING_SIGMA_MM` -- matching
`segment_pinna_threshold.segment_pinna_region()`'s own smoothing exactly,
NOT `smooth_for_thresholding`'s CurvatureFlow (scutum-pipeline-only) --
same "must match the pipeline's own smoothing" lesson this project has
hit before with the bone-threshold calibration.

The same point is carried forward in `WizardState.pinna_soft_tissue_seed`
and reused on the SCUTUM REVIEW page (page 6, later in the wizard),
combined with a `scutum_bone_seed` still placed there, for
`calibrate_bone_threshold()` (unchanged formula: midpoint of the two,
smoothed via `smooth_for_thresholding`/CurvatureFlow, matching that
page's own pipeline as before) -- so calibration is now 2 total clicks
(1 on pinna, 1 on scutum) instead of the old 3 (all on scutum), with the
same or better coverage: previously-dead `air_seed` is gone, and pinna
gets a calibration option it never had.

`core/threshold_seeds.py`'s `ThresholdSeeds` dataclass and `SEED_STEPS`
list (built for a 3-point sequence on one page) were removed along with
this -- each page now tracks its own single optional point as a plain
`Optional[Tuple[float,float,float]]` field on `WizardState`
(`pinna_soft_tissue_seed`, `scutum_bone_seed`), each owned by the page
that places it (`wizard_state.PAGE_OWNED_FIELDS`), matching this
project's general preference for the simplest structure that fits rather
than carrying a now-oversized abstraction forward.
`check_seed_plausibility()` (advisory bone-vs-soft-tissue sanity check)
was carried over as `check_bone_soft_tissue_plausibility()`, but --
confirmed while making this change, pre-existing, not a regression --
was never actually wired into `page_scutum_review.py`'s UI either before
or after this session; still available to wire in later if wanted.

**First real-Slicer test (2026-07-31, same day): fixed-offset formula
confirmed wrong, reverted to a real air click.** Thomas placed the
soft-tissue point clearly on the pinna, but the resulting segmentation
looked as if the threshold were too high (too little material counted as
skin) -- the calibrated value was -359 HU, but he'd already found ~-550
HU necessary for a good result on this scan by hand. Backing out the
math: -359 = soft_tissue_hu - 300, so the sampled soft-tissue HU was -59
-- a perfectly ordinary fat/soft-tissue reading, meaning the click itself
was almost certainly fine. The bug was in the fixed 300 HU margin
assumption: the real gap needed was ~491 HU (-59 to -550), not 300, and
there's no reason to expect that gap is constant across scans -- exactly
the "fixed constant doesn't generalize scan-to-scan" failure mode this
project has hit and fixed before (see the sheetness-feature saga above).
Solving for what a genuinely-sampled air point would need to be for
midpoint(air_hu, -59) to land at -550 gives air_hu ~= -1041 -- itself a
perfectly plausible real air reading, which is what motivated the fix
below rather than just retuning the constant again.

**Fix**: reverted to a real, per-scan air click, restoring the same
midpoint-of-two-directly-sampled-points mechanism `calibrate_bone_
threshold()` already uses successfully (see the seed-based calibration
section above -- that one worked on its first real test). The pinna
review page now has a 2-point calibration sequence again (open air, then
soft tissue -- `_CALIBRATION_STEPS` in `page_pinna_review.py`, the same
step-list shape `core/threshold_seeds.py`'s old `SEED_STEPS` used, kept
page-local now rather than in the shared module since only this page
needs a multi-step sequence). `calibrate_skin_threshold(image, air_seed,
soft_tissue_seed)` now takes both points and returns their midpoint,
same shape as `calibrate_bone_threshold`. New `WizardState.pinna_air_seed`
field (pinna-page-local, not reused elsewhere) alongside the existing
`pinna_soft_tissue_seed` (still carried forward to the scutum page,
unaffected by this fix); both live on one shared fiducial node
(`pinna_seed_fiducial_node`), matching how the original 3-point scutum
sequence used to share one node. Total calibration clicks are now 3
again (2 on pinna, 1 on scutum) -- back to the original count, but with
the previously-dead scutum air click now gone and replaced by a pinna
air click that's actually used.

**Second real-Slicer test (2026-07-31, same day): mechanism confirmed
correct, real residual gap found, more tuning deliberately deferred.**
Thomas re-ran calibration on the same scan and reported the real sampled
values: soft_tissue_hu=86, air_hu=-1016 -- both perfectly plausible
(confirms the -1041 back-calculation above was a good guess, and that the
click placements are fine). Midpoint = -465 ("roughly -470" per Thomas),
a big improvement on the first version's -359 (down from 191 HU off the
-550 target to 85 HU off) but still not quite there, and in the SAME
direction both times (the true value is always somewhat below the
predicted one).

Working hypothesis, not yet confirmed: the pinna is a thin, folded
structure (cartilage + skin, often 1-3mm), so true-boundary voxels
partial-volume with air from both sides at once and read closer to air
than a clean 50/50 blend of the two bulk materials would predict -- a
plain two-point midpoint has no way to capture that, it only knows the
two bulk materials clicked on. Notably consistent with `SKIN_AIR_
THRESHOLD`'s own pre-existing config.py comment (predates this whole
calibration feature): the default was already deliberately set as "a
generous margin... to reliably catch the full skin surface," i.e. this
threshold was always expected to sit measurably below the naive
tissue/air midpoint, for this same partial-volume reason.

**Deliberately NOT fixed further right now** -- offered Thomas a choice
(add another guessed safety-margin constant below the midpoint, vs.
leave the pure midpoint as-is and treat the residual gap as normal
per-scan slider adjustment) rather than picking a third constant guess
off one data point, matching this project's hard-won lesson from the
sheetness feature (multiple rounds burned exactly this way). **Thomas's
call: leave it as the plain midpoint for now** -- "good enough starting
value," wants to gather more real-scan data before tuning further, may
revisit. Do not add a margin constant unprompted; wait for Thomas to
raise it again, ideally with results from more than one scan so any
future constant isn't fit to n=1 again.

Also still open from the first version of this feature, unaffected by
this fix: does placing the soft-tissue point on the pinna page and
having it silently reused two pages later (on scutum review) make sense
to a surgeon without confusion (both pages' status/tutorial text mention
the other page explicitly, but this is unconfirmed to read clearly in
practice), and does resetting either page's calibration points behave as
expected across a Back/Next round-trip.

**Follow-up, 2026-07-31, later same day — both RESOLVED, confirmed by
Thomas:** see the "Two more calibration bugs found and fixed" entry under
"Current status" above for the full story; summarized here since it's
this section's code. (1) `page_scutum_review.py`'s calibration-sampling
crop switched from `roi_crop.crop_to_landmark_region()` (2 landmarks
only, ±15mm) to the new `roi_crop.crop_to_points_region()` (landmarks +
both seed points together), fixing a spurious "calibration point fell
outside the scan region" error for a bone point placed validly but
further than 15mm from the canal axis. (2) `page_pinna_review.py`'s
2-point calibration (open air, then soft tissue) is no longer optional —
`on_leave_next()` now blocks "Next" until both are placed, because
skipping it (e.g. typing a threshold into the slider by hand instead)
silently left `pinna_soft_tissue_seed` at `None`, making THIS page's
"Auto-Calibrate & Segment" quietly fall back to
`config.DEFAULT_BONE_THRESHOLD` (100) with no error regardless of where
the scutum bone marker was placed. The Skin threshold slider itself is
still fully adjustable either way; only placing the two points is now
required, and every user-facing string on that page was updated to say
so explicitly (not just the gating logic) per Thomas's request.

---

## Scutum finalize mesh quality (2026-07-31)

**Status: round 4 shipped, NOT yet real-Slicer tested — pick up here
first in a new session.** See "Round 4" at the end of this section for
the current approach and exactly what to ask Thomas to check. Rounds 1-3
below are superseded (all three tried to fix the finalized mesh by
re-tuning our own marching_cubes/smoothing pipeline; round 4 sidesteps
that pipeline entirely) but kept for the full history/reasoning trail.

Thomas reported that "Preview 3D Result" (the finalize step
in `page_scutum_review.py` -- crop/select-component/postprocess/mesh,
see "Interactive Segment Editor threshold rework" above) visibly
degrades an already-good, hand-tuned live segmentation: the exported
mesh looks noticeably less precise than what the live Segment Editor
widget shows before clicking the button.

**Root cause, confirmed by code inspection (not yet independently
disproven): two smoothing steps run between the live segmentation and
the exported mesh, both originally tuned for cleaning up automated-
threshold noise, neither appropriate for a mask the surgeon already
hand-refined via live Threshold/Paint/Erase:**
1. `postprocess.smooth_boundary()` — 2 rounds of binary morphological
   closing+opening (voxel-level).
2. `mesh_export._verts_faces_to_trimesh()`'s Laplacian smoothing —
   `MESH_SMOOTHING_ITERATIONS=15` (vertex-level), applied unconditionally
   to every mesh from every pipeline (pinna and scutum both), not scutum-
   specific.

**Round 1 (shipped, real-Slicer confirmed PARTIALLY): disabled step 1
entirely for scutum, reduced step 2's iteration count.** Added
`smooth: bool = True` to `postprocess.run_full_postprocess()` (default
preserves existing behavior everywhere else; scutum's finalize call
passes `smooth=False`) and a `smoothing_iterations` parameter to
`mesh_export.label_map_to_mesh()` (default `MESH_SMOOTHING_ITERATIONS`;
scutum passes a new `config.SCUTUM_MESH_SMOOTHING_ITERATIONS`, first set
to 2 — matching `smooth_boundary()`'s own "intentionally mild" count).
**Real-Slicer result**: shape preserved much better, but Thomas reported
the mesh now looks "too blocky" — 2 iterations of Laplacian isn't enough
to hide marching_cubes' voxel-grid staircase terracing.

**Round 2 (shipped, real-Slicer confirmed WORSE — a genuine regression,
reverted in round 3):** tried `trimesh.smoothing.filter_taubin()`
(alternating shrink/inflate passes, the textbook fix for Laplacian's
staircase-vs-shrinkage tradeoff) at 8 iterations instead of plain
Laplacian, based on a synthetic hollow-shell-with-thin-ridge test
(scratchpad, not committed) showing Taubin's behavior was more stable
across iteration counts than Laplacian's. **Real-Slicer result: WORSE on
both axes** — Thomas reported it was "still very blocky" AND "somehow
made it the wrong shape too." **Lesson, important**: both Laplacian and
Taubin operate on mesh VERTICES after marching_cubes has already snapped
the surface to the voxel grid — moving vertices around after the fact
can only trade blockiness against shape distortion on the same knob,
never fix both at once, regardless of which vertex-smoothing algorithm
is used. The synthetic test's inconclusive/noisy numbers (all vertex-
smoothing configs tried — Laplacian, Taubin, Humphrey — scored within
noise of each other) should have been a stronger warning sign than it
was treated as at the time.

**Round 3 (shipped, NOT YET real-Slicer tested — this is where the next
session picks up): structurally different approach — blur the MASK
before marching_cubes, not the mesh after.** New
`mesh_export.label_map_to_mesh(..., mask_blur_sigma_mm=...)`: casts the
label image to float, runs `sitk.SmoothingRecursiveGaussian` (physical mm
sigma) BEFORE marching_cubes, then extracts the isosurface from the
blurred continuous field at the same `level=0.5` — recovers genuine
sub-voxel surface position (same idea as
`mesh_export.label_map_to_mesh_subvoxel()`, but works without a single
known threshold value, so it's safe for Paint/Erase-edited masks). Safe
by construction against the "reopens what postprocessing closed" concern
`label_map_to_mesh_subvoxel()` had to guard against (see "Improving Canal
Segmentation" below): this blurs only the mask's own already-decided
material, never the original intensity field, so it can't reintroduce
anything postprocessing already excluded. Scutum's finalize call now
uses `smoothing_iterations=0` (no vertex-space smoothing at all —
deliberately, to remove that variable entirely after round 2's
regression) plus `mask_blur_sigma_mm=config.SCUTUM_MESH_MASK_BLUR_SIGMA_MM`
(0.6mm).

**Validation so far (synthetic + one real-code smoke test, NOT real
Slicer)**: same hollow-shell-with-thin-ridge synthetic geometry as
round 2's test, at 0.4mm spacing / 2mm wall thickness. At sigma=0.6mm
this approach beat every vertex-smoothing method tried (Laplacian,
Taubin, Humphrey) on BOTH staircase-removal AND real-ridge-preservation
SIMULTANEOUSLY — not just a different point on the same tradeoff curve —
the first synthetic result in this whole saga to actually show that. Too
little sigma (≤0.4mm here) barely helped staircase; too much (≥0.8mm)
started eroding real detail and even made terracing WORSE (the wall's
two surfaces blurring into each other). Also ran a smoke test of the
actual shipped `mesh_export.label_map_to_mesh()` code path (not just the
standalone synthetic script) against a synthetic `sitk.Image` — confirmed
it runs without error, produces a watertight mesh, and volume shifts by
under 3%.

**Honest caveats, not yet resolved:**
- 0.6mm was tuned against a 2mm-thick synthetic wall at 0.4mm spacing.
  Real scans vary in both wall thickness and native spacing — if a
  particular scan's wall is much thinner, or spacing much finer/coarser,
  this sigma may need adjusting. `config.SCUTUM_MESH_MASK_BLUR_SIGMA_MM`
  is the one constant to revisit if so.
- This is the THIRD attempt at this specific problem in one day, and the
  second one (Taubin) was confidently reasoned about, synthetically
  tested, and still came out worse on the real scan. Don't report this as
  "fixed" to Thomas until he's actually re-run "Preview 3D Result" and
  confirmed it — ask directly, the same way prior rounds were confirmed
  or refuted.
- **Next session, concretely**: ask Thomas to click "Preview 3D Result"
  again on the same scan/segmentation he's been testing with, and report
  specifically (a) is the blockiness gone or reduced, (b) does the shape
  now match what the live Segment Editor showed before finalizing. If
  either is still wrong, the next lever to consider is
  `SCUTUM_MESH_MASK_BLUR_SIGMA_MM` (try smaller if still blocky in a
  different way than before, larger if under-smoothed) — NOT a return to
  vertex-space smoothing, which has now failed twice on this exact
  problem.

  (Superseded by round 4 below before this was ever real-Slicer tested —
  Thomas asked to explain the finalize pipeline for a fresh look instead,
  which led to round 4's different diagnosis.)

**Round 4 (shipped 2026-07-31, later same day — NOT yet real-Slicer
tested, this is where the next session picks up): root cause reframed
entirely, and the whole crop/postprocess/marching_cubes rebuild replaced
with reusing Slicer's own already-good mesh.** Asked to explain the
finalize pipeline from scratch (fresh eyes, not another blind constant
retune), which surfaced something rounds 1-3 never questioned: **why does
the live Segment Editor view look good, but the finalized mesh doesn't,
when both supposedly come from "the same segmentation"?**

Checked against Slicer's own source (`vtkBinaryLabelmapToClosedSurfaceConversionRule.cxx`,
via WebFetch, not assumed from memory — this project's established
practice, see [[feedback_verify_before_trusting_apis]]): the live 3D view
is Slicer's own auto-generated closed surface,
`vtkDiscreteFlyingEdges3D` → `vtkWindowedSincPolyDataFilter` at Slicer's
default smoothing factor 0.5. The old finalize step, meanwhile, re-derived
a COMPLETELY SEPARATE mesh — `ExportVisibleSegmentsToLabelmapNode` →
crop → component-select → `postprocess.run_full_postprocess()` →
`mesh_export.label_map_to_mesh()`'s own `skimage.measure.marching_cubes`
+ mask-blur/vertex-smoothing (rounds 1-3's tuning target). **Two
genuinely different meshing/smoothing algorithms running on
similar-but-not-identical voxel data** — this is why 3 rounds of tuning
constants on OUR OWN pipeline never converged on what Slicer's pipeline
already looked like: rounds 1-3 were all tuning the wrong variable.

Also directly answered a natural follow-up question (is the labelmap
export itself lossy?) rather than leaving it assumed: **no** —
`ExportVisibleSegmentsToLabelmapNode` is not resampling/requantizing
anything. Segment Editor's Threshold/Paint/Erase effects already edit a
binary labelmap directly as their native representation, at
`state.volume_node`'s own resolution (no oversampling factor is
configured anywhere on this page) — exporting it is a plain data copy.
The mismatch was always downstream of that, in which algorithm turns the
(identical) voxels into a surface.

**Fix implemented**: finalize now pulls the segmentation's OWN
closed-surface mesh directly, via `ExportVisibleSegmentsToModels`
(confirmed via Slicer's script-repository docs, alongside
`ExportAllSegmentsToModels`, to return model nodes in world/RAS
coordinates — deliberately NOT `GetClosedSurfaceRepresentation()`
directly, which those same docs say returns the segmentation node's own
internal coordinate system and would need a manual parent-transform
correction, not worth the risk on a project with this much RAS/LPS
history). New `page_scutum_review.py` methods:
`_export_segment_as_trimesh()` (Slicer/VTK-facing: exports via a
temporary subject-hierarchy folder + model node, converts the polydata to
`trimesh.Trimesh` via `_polydata_to_trimesh()`, cleans up the temp nodes)
and, in `core/mesh_export.py` (Slicer-independent, synthetic-testable):
`crop_mesh_to_vertex_mask()` (mesh-space equivalent of the old
`label_array & roi_array` intersection — keeps only faces whose 3
vertices all pass `roi_crop.points_inside_roi()`, a new function factored
out of `build_roi_mask()`'s inside-test closure so both the voxel and
mesh paths share identical cylinder-ROI math) and
`select_mesh_component_nearest_axis()` (mesh-space equivalent of
`segment_threshold._closest_component_to_axis_line()` — splits into
connected components via `trimesh.split()`, keeps the one whose vertex
mean is closest to the canal axis line).

**EXPERIMENTAL, Thomas's explicit call**: this also means
`postprocess.run_full_postprocess()` no longer runs AT ALL for scutum
finalize — no speck removal, no hole filling, and critically no
`close_small_tunnels()` (this region is "genuinely tube-shaped" per
`TUNNEL_CLOSING_RADIUS_MM`'s own comment, i.e. prone to real topological
handles — exactly the failure mode the 8-round Isolate Patch saga fought
to fix, and `page_scutum_draw.py` feeds this exact mesh into the same
`mesh_isolate.isolate_surface_patch()` flood-fill engine). I flagged this
risk explicitly before implementing (offered a lower-risk alternative:
keep the existing voxel pipeline including `close_small_tunnels()`
unchanged, and only swap the FINAL step to push the cleaned mask through
a temp segmentation node so Slicer's own converter meshes it — smaller
diff, same benefit, no topology risk). **Thomas's call: skip postprocess
entirely anyway** — his reasoning is that hole/speck/tunnel cleanup may
not matter here since this mesh only needs to accurately depict the
scutum, not be a polished/watertight display asset, and he'd rather test
this empirically on the draw page than assume it'll be a problem.

**Validation so far**: synthetic-only (scratchpad, not committed) —
confirmed `roi_crop.points_inside_roi()` produces bit-for-bit identical
results to `build_roi_mask()`'s voxel test on the same geometry (pure
refactor, no behavior change to the existing voxel path), and confirmed
`crop_mesh_to_vertex_mask()` + `select_mesh_component_nearest_axis()`
correctly reject an unrelated far-away blob and correctly pick the
TRULY-closest of two components near the axis (not just the first one
found) on synthetic multi-component meshes. This validates the new
mesh-space math is correct — it does NOT validate real-scan mesh quality
or the topology/Isolate-Patch question, which per this project's own
repeated lesson ([[feedback_synthetic_tests_limits]]) only a real-Slicer
test can answer.

**Next session, concretely — two separate things to ask Thomas to
check, in this order:**
1. Click "Preview 3D Result" on a real scan/segmentation. Does the
   finalized mesh now visually match the live Segment Editor view (the
   original complaint this whole thread started from)? This is the part
   round 4's fix directly targets and should be strong evidence for.
2. **Separately**, go to the scutum draw page and actually run Isolate
   Patch on that finalized mesh. Does it work normally, or does it hit
   `LoopDoesNotSeparateError` / need the fallback-retry path / behave
   differently than it used to? This is the deliberately-untested risk
   from skipping postprocess — don't assume it's fine just because step 1
   looked good, they're testing different things. If Isolate Patch
   breaks, the fallback is porting tunnel-closing-equivalent cleanup to
   mesh space (e.g. `trimesh.repair.fill_holes()`, which
   `mesh_export._verts_faces_to_trimesh()` already uses unconditionally
   for the OLD marching_cubes-based meshes — notably, round 4's new mesh
   path never calls this either, another piece of the "skip postprocess"
   experiment, not an oversight), NOT reverting to the labelmap-based
   approach (that would bring back the original mesh-quality mismatch
   this round exists to fix).

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
  optional). *As originally shipped* (this bullet describes that original
  design; see "Calibration-seed cleanup" under "Interactive Segment
  Editor threshold rework" below for how this was reworked 2026-07-31 --
  the 3 points are no longer all on this page, `calibrate_thresholds()`
  no longer exists, and the mechanism for the skin/air side changed twice
  in one day): surgeon clicks 3 points (air lumen / bone / soft tissue);
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
   pinna-first reorder, `slicer.util.restart()` (Setup page's restart
   button, 2026-07-30), and (2026-07-30) `base_page.WizardPage.
   run_blocking()`'s core assumption -- that calling
   `slicer.app.processEvents()` from a polling loop on the main thread
   while a background Python thread runs SimpleITK/skimage/scipy/trimesh
   work keeps Slicer's window responsive -- verified against a faked
   `slicer.app.processEvents()` (scratchpad, not committed), NOT yet
   against Slicer's real Qt/VTK event loop. If "Not Responding" still
   appears after this fix, that's the first thing to re-examine.
5. ~~(2026-07-30) `page_scutum_review.py`'s embedded `qMRMLSegmentEditorWidget`~~
   **CONFIRMED working (2026-07-30, same day)** -- see "Interactive
   Segment Editor threshold rework": instantiating the widget class
   directly and inserting it into a scripted module's own `.ui` layout
   (rather than reusing the SegmentEditor module's singleton instance),
   `setEffectNameOrder`/`unorderedEffectsVisible` to curate the effect
   list, and driving the Threshold effect via `effect.setParameter
   ("MinimumThreshold"/"MaximumThreshold", ...)` + `effect.self().onApply()`
   all function in practice, not just per-docs. (Some unspecified
   interface complaints remain -- see "Current status" above -- but the
   core API surface itself is confirmed, not untested.)
6. **Once the pinna anisotropy fix is confirmed across more scans: remove
   the temporarily-forced-visible `restartSlicerButton`** (`page_setup.py`'s
   `_refresh_status()`, added 2026-08-22 -- see "Pinna mesh decimation
   topology bug" above for the full context). Right now it's hard-coded
   to always show, purely to speed up Thomas's repeated restart-and-retest
   cycle while chasing the pinna anisotropy bug. **Status as of
   2026-08-25: the underlying fix is confirmed working on the first real
   scan it was reported on ("much better"), but Thomas wants to test more
   scans before calling it fully resolved** -- leave this button and the
   diagnostics below in place until that happens. Once it's confirmed
   general, restore the original behavior (hidden once nothing needs
   installing, so a returning surgeon doesn't see an unexplained restart
   option) -- change that one `setVisible(True)` back to `setVisible(False)`
   in the `if not missing:` branch. At the same time, sweep out this
   session's TEMPORARY markers: `[mesh_export diag]` prints in
   `core/mesh_export.py`, `[pinna diag]` component-count checks in
   `page_pinna_review.py`, and the `[pinna diag] resample_to_bounded_
   anisotropy: ...` print in `core/io_utils.py`.

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
