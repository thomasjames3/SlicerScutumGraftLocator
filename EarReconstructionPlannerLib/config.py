"""
config.py
=========
Every "magic number" in this project lives here, in one place, with a plain-
English explanation of what it controls. If you (or a future collaborator)
need to tune the pipeline, this is the only file you should need to touch --
you should never need to hunt through core/ to find a hardcoded value.

Nothing in here is Slicer-specific, so this file can be imported both by the
Slicer module and by plain command-line scripts / tests.
"""

# ---------------------------------------------------------------------------
# Volume preprocessing
# ---------------------------------------------------------------------------

# All scans get resampled to this isotropic voxel spacing (in mm) before any
# processing happens. This keeps distances/thresholds comparable across
# scans that were acquired with different protocols.
TARGET_VOXEL_SPACING_MM = 0.3

# Sigma (in mm) for the Gaussian smoothing pass that reduces noise before
# thresholding. Matches the "sigma = mean voxel size" rule of thumb used in
# the Matin-Mann et al. (2025) EEC segmentation paper.
#
# Only used by the PINNA pipeline now (core/segment_pinna_threshold.py),
# which is already confirmed working end-to-end and wasn't showing the
# problem CURVATURE_FLOW_TIME_STEP/_ITERATIONS below were introduced to
# fix -- deliberately left alone rather than switched over too. The
# ear-canal (scutum) pipeline uses core/smoothing.py instead; see that
# module's docstring for why plain Gaussian blur was replaced there.
GAUSSIAN_SMOOTHING_SIGMA_MM = TARGET_VOXEL_SPACING_MM

# Pre-thresholding denoise parameters for the ear-canal (scutum) pipeline
# -- see core/smoothing.py for the full mechanism and the evidence that
# led here. A real reported failure was the scutum and malleus reading as
# fused in the exported mesh: the thin true air gap between them was
# getting blurred above bone_threshold by the (formerly plain Gaussian)
# smoothing pass before thresholding ever ran, which no threshold value
# could fix after the fact. CurvatureFlow (edge-preserving diffusion) was
# chosen over plain Gaussian, a median filter, and a bilateral filter
# after comparing all four on synthetic data along both axes that matter:
# noise suppression (spurious connected components surviving thresholding
# of a uniformly noisy region right at the threshold value -- the worst
# case for segment_threshold._closest_component_to_axis_line's selection)
# and thin-gap preservation (how close a true air gap's minimum intensity
# stays to its real value after smoothing). At these settings, measured
# noise suppression was ~40 residual components vs. plain Gaussian's ~37
# (effectively tied), while a true 0.6mm gap's minimum intensity stayed at
# -700 (its real value) vs. Gaussian's -128 -- uncomfortably close to a
# typical bone_threshold around 100. Median and bilateral filters
# preserved gaps just as well but suppressed noise noticeably worse (205
# and 313 residual components respectively).
#
# Iteration count is the main lever if this ever needs retuning: fewer
# iterations = less smoothing (better gap preservation, worse noise
# suppression, faster); more = the opposite. ~30x slower than the plain
# Gaussian pass on a realistic crop (~270ms vs ~9ms at 10 iterations) --
# immaterial for an explicit "Run Segmentation" click, but worth knowing
# if iterations are ever raised substantially.
CURVATURE_FLOW_TIME_STEP = 0.01
CURVATURE_FLOW_ITERATIONS = 10

# ---------------------------------------------------------------------------
# ROI (region of interest) cropping
# ---------------------------------------------------------------------------

# Starting diameter (in mm) of the truncated-cylinder volume of interest
# built from the 2 landmark points. This just needs to comfortably contain
# the ear canal; the segmentation step below refines the actual boundary.
INITIAL_ROI_DIAMETER_MM = 20.0

# How far (in mm) the ROI cylinder's two end-planes extend past
# canal_opening/near_eardrum, along the canal axis. Both planes are always
# perpendicular to that axis (see roi_crop.build_roi_mask() for why this
# replaced the original 4-landmark tilted-plane design) -- generous on
# purpose so a slightly short canal_opening/near_eardrum placement still
# comfortably contains the true wall, without needing any extra clicks.
ROI_AXIAL_MARGIN_MM = 5.0

# ---------------------------------------------------------------------------
# Stage A: threshold-based segmentation (no training data required)
# ---------------------------------------------------------------------------
#
# The final output of this pipeline is the BONE WALL of the ear canal, not
# the air-filled lumen. However, segmenting the air lumen first is still the
# key trick: air-vs-tissue contrast is far cleaner and more reliable than
# bone-vs-soft-tissue contrast, so we use the air lumen as an internal
# scaffold to know *where* the bone wall is, then threshold for bone only
# within a thin shell around that scaffold. The air mask itself is never
# exported -- see segment_threshold.segment_bone_wall().

# Hounsfield-Unit-like threshold separating air (low intensity) from
# everything else. Used only internally to find the air lumen scaffold.
DEFAULT_AIR_THRESHOLD = -300

# Range the review-step slider is allowed to move the air threshold within.
# Kept narrow on purpose so a surgeon can't accidentally produce a wildly
# wrong segmentation just by dragging too far.
AIR_THRESHOLD_ADJUST_RANGE = (-600, 0)

# Hounsfield-Unit-like threshold separating bone (high intensity) from
# soft tissue (lower intensity). This is the value that actually determines
# the exported bone wall boundary. Lowered to 100 (from 300) after Thomas's
# own threshold experimentation found the segmentation runs best with bone
# at its lowest tested value, 100.
DEFAULT_BONE_THRESHOLD = 100

# Range the review-step slider is allowed to move the bone threshold within.
# Widened below the old 100 default (previously the range bottomed out
# exactly at 100, leaving no room to try lower values) so a surgeon can
# still explore lower thresholds if 100 isn't optimal for a given scan.
#
# Upper bound raised from 700 to 2000 after real-Slicer use of seed-based
# calibration (core/threshold_seeds.py) routinely computed a bone_threshold
# above 700 and got silently clamped there -- real cortical bone HU on a
# given scan can legitimately sit well above 700 (dense temporal bone is
# commonly 1000-2000+ HU), and per-scan calibration sampling actual bone
# intensity is a more reliable signal than the old fixed default (100,
# deliberately lowered from 300 to compensate for partial-volume blending
# on an *uncalibrated* threshold) for how high this can reasonably go. 2000
# comfortably covers dense cortical bone while still ruling out clearly
# implausible values.
BONE_THRESHOLD_ADJUST_RANGE = (-200, 2000)

# Expected thickness (in mm) of the bony ear canal wall. This defines how
# far outward from the air-lumen scaffold we look for bone. Generous on
# purpose -- it only needs to comfortably contain the true wall thickness,
# since the bone threshold itself (not this number) determines the final
# boundary. If the exported mesh looks like it's missing wall on one side,
# this is the first number to increase in config.py.
BONE_WALL_THICKNESS_MM = 3.0

# Connected-component analysis uses a 6-connected 3D neighborhood (matches
# the EEC paper) rather than 26-connected, which is more conservative and
# less likely to bridge across a thin bone wall by accident.
CONNECTED_COMPONENT_CONNECTIVITY = 1  # scipy.ndimage.label(structure=...) uses 1 = 6-connectivity in 3D

# Dead end -- do not reintroduce: an EPITYMPANUM_SEARCH_RADIUS_MM constant
# and matching _closest_component_to_point() lookup in segment_threshold.py
# once unioned a second (near_eardrum-proximity) air component into the
# lumen scaffold, trying to include the epitympanum so the bone-wall shell
# would reach around it. Confirmed on a real scan: this damaged real canal
# wall coverage (missing strips on the anterior and posterior wall,
# widespread, not localized to the epitympanum region) without actually
# fixing the scutum/malleus fusion it was meant to help with. Reverted.
# If this gets revisited, the next attempt should be a dedicated 3rd
# landmark click placed directly inside the epitympanum, not an automatic
# near_eardrum-proximity guess -- see git history for the removed code.

# ---------------------------------------------------------------------------
# Stage A: seed-based threshold calibration (optional convenience)
# ---------------------------------------------------------------------------
#
# DEFAULT_AIR_THRESHOLD/DEFAULT_BONE_THRESHOLD above are one fixed HU pair
# used for every patient/scanner. A synthetic 150-patient experiment (run
# against the real segment_threshold.segment_bone_wall() function, varying
# per-patient HU calibration drift, noise, blur, and anatomy) found that
# deriving both thresholds instead from 3 quick surgeon seed-clicks (air
# lumen / bone / general soft tissue -- see core/threshold_seeds.py) raised
# mean Dice from 0.599 to 0.650, and the improvement roughly doubled
# specifically under simulated scanner HU drift vs. a no-drift control --
# confirming this really corrects inter-scan calibration drift, not just
# averages out noise. This is optional: the review page's sliders remain
# the actual source of truth, and seed calibration only pre-fills their
# starting values (see page_scutum_review.py).

# Minimum plausible difference (in Hounsfield-Unit-like intensity) between
# the bone seed and soft-tissue seed's sampled values. If the difference is
# smaller than this, one of the two clicks probably landed in the wrong
# place (most likely the "bone" click actually landed on soft tissue) --
# core/threshold_seeds.check_seed_plausibility() uses this to show an
# advisory warning (not a hard block; the surgeon can still proceed and
# manually adjust the sliders afterward). Set well below a typical
# bone-vs-soft-tissue HU gap (hundreds of HU) so this only fires on a
# clearly implausible pair of clicks, not routine per-patient variation.
MIN_BONE_SOFT_TISSUE_SEPARATION_HU = 50.0

# ---------------------------------------------------------------------------
# Stage B: trained-model segmentation (used once a model exists)
# ---------------------------------------------------------------------------

# Where a trained nnU-Net model is expected to live. If this path doesn't
# exist, segment_dl.py automatically falls back to Stage A -- the surgeon-
# facing tool always works, with or without a trained model.
TRAINED_MODEL_DIR = "models/ear_canal_nnunet"

# Minimum number of approved, banked training cases before train_model.py
# will let you kick off a training run. Below this, the model tends to
# overfit and underperform Stage A, so we simply don't offer it yet.
MIN_CASES_TO_TRAIN = 15

# ---------------------------------------------------------------------------
# Postprocessing
# ---------------------------------------------------------------------------

# After segmentation, small disconnected specks smaller than this volume
# (in mm^3) are removed automatically as noise.
MIN_COMPONENT_VOLUME_MM3 = 5.0

# Radius (mm) of a closing-only (dilate then erode) pass run before
# postprocess.smooth_boundary(), specifically to seal small tunnels/
# handles straight through otherwise-solid tissue -- confirmed on a real
# scan (2026-07-27) that these are a genuinely different defect from an
# open boundary hole: a mesh can be fully watertight (is_watertight=True,
# no open edges) while still having several handles (mesh.euler_number
# well below 2), which postprocess.fill_holes() and mesh_export.py's
# trimesh.repair.fill_holes() both cannot fix, since neither has any open
# boundary to patch -- a tunnel through sealed material is more like a
# torus than a hole. smooth_boundary()'s existing closing+opening pair
# uses a fixed 1-voxel radius (~0.45mm at typical scan spacing), too
# narrow to bridge tunnels wider than that.
#
# Deliberately closing-only (no matching opening/erosion step) -- closing
# only ADDS material to bridge small gaps, it can never remove/erode
# existing material, so unlike smooth_boundary's opening step (or any of
# the spike-removal machinery in segment_pinna_threshold.py), this can't
# cause the kind of erosion that ate the pinna's helix (Known Issues #15/
# #18 in CLAUDE.md). Expressed in mm (converted to a per-axis voxel
# radius from the scan's own spacing), like other physically-scaled
# kernels in this project. If tunnels/handles persist, raise this; if it
# ever visibly merges two anatomically-separate structures that should
# stay apart, lower it.
#
# NOT applied by default: postprocess.run_full_postprocess()'s
# close_tunnels defaults to False (2026-07-28 fix). At 2mm this closing
# radius also seals real ~1-3mm anatomical folds (helix rim, antihelix,
# concha bowl) on the pinna's raw skin-surface segmentation, degrading
# the surgeon-facing Run Segmentation result with no matching benefit --
# genus>0 handles only ever showed up post-isolation. Only pass
# close_tunnels=True where actually validated: the pinna isolate-fallback
# path (page_pinna_draw.py) and scutum's bone-wall shell
# (page_scutum_review.py), which is genuinely tube-shaped.
TUNNEL_CLOSING_RADIUS_MM = 2.0

# Mesh smoothing iterations applied before STL export. Higher = smoother
# but less true to the raw voxel boundary.
MESH_SMOOTHING_ITERATIONS = 15

# mesh_export.label_map_to_mesh_subvoxel() extracts the scutum bone-wall
# mesh from the actual smoothed grayscale field near the mask boundary,
# instead of the already-binarized mask (which snaps every vertex to the
# voxel grid) -- added after real-Slicer use surfaced a general precision
# complaint on small/thin bone regions. How far (mm, on both sides of the
# boundary) to trust the real intensity before blending toward a safety-
# clamped constant; see that function's docstring for the full mechanism.
# Set to 2x GAUSSIAN_SMOOTHING_SIGMA_MM -- wide enough to comfortably
# contain where a Gaussian blur of that sigma actually spreads a sharp
# edge's partial-volume transition, narrow enough to stay well clear of
# other same-density structures elsewhere in the (already-cropped, but not
# empty) ROI, e.g. the mastoid or ossicles -- see segment_threshold.py's
# shell-restriction docstring for why those are real, previously-observed
# neighbors within this same crop.
#
# Tuning note: a discrete voxel can never sit closer than one full
# TARGET_VOXEL_SPACING_MM from an opposite-class voxel, so the blend
# weight at even the single closest boundary-adjacent voxel is always at
# least spacing/band_radius_mm -- at this default (2x the spacing), that
# floor is 0.5, meaning SUBVOXEL_MESH_SAFETY_MARGIN_HU's clamp value
# already makes up half of even the closest voxel's blended intensity.
# Synthetic testing confirmed this still gives a real, consistent
# (if modest -- roughly 15-20% RMS reduction in boundary position error
# in that test) accuracy improvement, while never reopening a
# postprocessing-closed tunnel even under an adversarial stress test. If
# real-scan use shows the effect is too weak to matter, raising this
# constant is the first lever to try -- it trades some of that
# tunnel-reopening safety margin (and a bit more risk of the "unrelated
# nearby structure" issue described above) for a stronger pull toward the
# true intensity near the boundary.
#
# Set as a direct mm value (was previously derived as
# 2 * GAUSSIAN_SMOOTHING_SIGMA_MM, back when the canal pipeline's own
# pre-thresholding smoothing was a plain Gaussian blur with that sigma;
# it now uses core/smoothing.py's CurvatureFlow-based denoise instead,
# which has no single "sigma" to derive from -- see CURVATURE_FLOW_
# TIME_STEP/_ITERATIONS above). Kept at the same 0.6mm this formula used
# to produce, since that value's reasoning (2x TARGET_VOXEL_SPACING_MM)
# still holds on its own terms.
SUBVOXEL_MESH_BAND_MM = 0.6

# How far (in Hounsfield-Unit-like intensity, on both sides of
# threshold_value) label_map_to_mesh_subvoxel() pushes the blended field
# once outside SUBVOXEL_MESH_BAND_MM, to guarantee marching_cubes can't
# pick up a spurious extra surface component from an unrelated same-HU
# structure far from the true boundary. Just needs to comfortably clear
# realistic within-ROI intensity variation on either side of a typical
# threshold value -- not physically meaningful beyond that, so not worth
# tying to a real calibrated HU range.
SUBVOXEL_MESH_SAFETY_MARGIN_HU = 1000.0

# ---------------------------------------------------------------------------
# Post-segmentation wall-thickness warning
# ---------------------------------------------------------------------------
#
# A second synthetic 150-patient diagnostic (same set as the seed-
# calibration experiment above, instrumented) found catastrophic
# segmentation failures (Dice ~0) are NOT caused by the internal air-lumen
# scaffold failing (its Dice stayed ~0.90-0.97 even in failing cases,
# uncorrelated with wall failure, r=-0.15) -- they're caused by the TRUE
# anatomical wall being thin relative to the scan's effective blur/PSF:
# partial-volume effect smears a thin wall's intensity into its neighbors
# before any threshold (fixed or seed-calibrated) can separate it. True
# wall thickness alone correlated r=+0.83 with final wall Dice; noise
# correlated ~0.05. No threshold choice can fix this failure mode, so
# core/wall_quality.py detects and flags it instead of silently producing
# a confidently-wrong-looking mesh.

# Minimum acceptable local wall thickness (mm) before
# wall_quality.check_wall_thickness() warns the surgeon. Chosen as roughly
# 3x TARGET_VOXEL_SPACING_MM (0.3mm) -- a common rule-of-thumb minimum-
# resolvable-feature-size margin -- and cross-checked against where the
# synthetic experiment's Dice actually cratered: mean Dice 0.24 when
# true-wall-thickness/blur-sigma ratio was below 1.5, vs. 0.70+ above a
# ratio of 2.5. KNOWN LIMITATION: this compares against the pipeline's
# post-resample spacing (TARGET_VOXEL_SPACING_MM), not the scan's true
# native acquisition resolution -- native slice thickness isn't currently
# tracked anywhere in this pipeline, so a scan with coarser native
# resolution than the resample target could still hit this failure mode
# without tripping this check as early as it ideally should. Good
# candidate for a future improvement (track native spacing through
# io_utils and factor it in here); not needed for v1, since the check
# still fires correctly whenever the resampled data itself can't resolve
# the wall, which is the failure mode actually observed so far.
MIN_SAFE_WALL_THICKNESS_MM = 1.0

# ---------------------------------------------------------------------------
# File locations (relative to the project's data directory)
# ---------------------------------------------------------------------------

TRAINING_CASES_DIR = "training_cases"   # where approved cases get banked
EXPORT_DIR = "exports"                  # where final STL files land

# ---------------------------------------------------------------------------
# Pinna isolation
# ---------------------------------------------------------------------------
#
# Unlike the ear canal, we are NOT trying to isolate cartilage by intensity
# here -- cartilage-vs-skin contrast on CT is too poor and inconsistent to
# threshold reliably (this was a known gap in the published literature when
# we researched this earlier). Instead, Stage A finds the outer SKIN
# surface (skin-vs-air contrast is excellent, same trick as the ear canal's
# air-vs-bone approach) in a region around the ear. The surgeon's drawn
# outline is what actually separates "just the pinna" from the surrounding
# scalp/cheek/neck skin -- the threshold step just narrows the search area
# and gives them a clean surface to draw on.

# Hounsfield-Unit-like threshold separating skin/soft tissue (above) from
# surrounding air (below). Typical skin-air boundaries sit well above this,
# so a generous margin is used to reliably catch the full skin surface
# even with some scan noise.
SKIN_AIR_THRESHOLD = -300

# Range the review-step slider is allowed to move the skin threshold within.
# Widened below the old -600 floor (same reasoning as BONE_THRESHOLD_ADJUST_RANGE
# above): Thomas's own bone-threshold experimentation found this scan's
# intensity values needed a much lower threshold than the standard-HU
# defaults assumed, which suggests this scan's values may be shifted from
# conventional Hounsfield units -- if so, the skin/air boundary may need a
# lower threshold too. Widened so that can be tried via the slider without
# editing this file by hand.
SKIN_THRESHOLD_ADJUST_RANGE = (-900, 0)

# Radius (in mm) of the spherical region of interest built around the
# surgeon's single ear-center landmark. Generous on purpose -- it just
# needs to comfortably contain the whole pinna plus a margin of surrounding
# skin for the surgeon to draw against; it is not the final pinna boundary.
PINNA_ROI_RADIUS_MM = 45.0

# Minimum plausible distance (mm) between the two ears in an adult head,
# used only as a sanity check if both ear-center points are ever provided
# in the same session (e.g. planning bilateral reconstruction).
MIN_INTERAURAL_DISTANCE_MM = 100.0

# Morphological opening radius (mm) applied to the pinna region mask right
# after component selection. Fixes a recurring cosmetic artifact Thomas
# flagged (a thin "skinny line" sticking out of the pre-draw pinna model,
# usually near the top): the spherical PINNA_ROI_RADIUS_MM boundary can
# graze the scalp surface almost tangentially, leaving a thin wedge of
# tissue attached to the main blob by a narrow neck. An opening (erode
# then dilate) at this radius severs a neck that thin without visibly
# affecting the much thicker real anatomy that makes up the rest of this
# stage's blobby "head skin near the ear" mask -- this stage isn't yet
# isolating the delicate ear folds themselves (that's the surgeon's drawn
# outline, on the next page), so a small radius here is safe. Expressed in
# mm (converted to a per-axis voxel radius from the scan's own spacing),
# not a fixed voxel count, so it behaves consistently across scans with
# different spacing. If a real thin feature ever gets clipped, lower this;
# if the spike survives, raise it.
#
# A 2026-07-27 real-scan report of a circular hole on the pinna's helix
# looked at first like this radius eating real anatomy, and a same-day
# attempt to restrict this opening to a shell near the ROI's own edge (see
# segment_pinna_threshold._remove_boundary_spike's docstring) was tried
# and reverted -- the actual cause turned out to be an unrelated bug (see
# page_pinna_review.py's `_segmentation_edited` guard), not this radius or
# this function. Applied to the whole mask, as originally confirmed
# working by Thomas.
PINNA_SPIKE_REMOVAL_RADIUS_MM = 1.5

# After the surgeon draws and isolates the pinna outline, any remaining
# part of the mesh toward the interior of the head (past the ear canal
# opening) is cropped away automatically -- the drawn outline marks the
# pinna's outer boundary, but the isolated patch can still include a bit
# of the head-side attachment near the canal. The cut plane sits at the
# ear canal opening (core/landmarks.py's canal_opening landmark),
# perpendicular to the canal axis, extended outward by this margin so the
# pinna's own tissue right at its base isn't clipped by an imprecisely
# placed landmark.
PINNA_CANAL_CROP_MARGIN_MM = 2.0

# ---------------------------------------------------------------------------
# Draw pages (scutum + pinna outline drawing)
# ---------------------------------------------------------------------------

# Absolute (not screen-relative) size, in mm, used for Markups
# curve/fiducial points placed on the draw pages (the drawn outline's own
# points, the seed point, the pinna's canal-opening marker). Slicer's
# default point size is a PERCENTAGE of screen size
# (vtkMRMLMarkupsDisplayNode.GlyphScale), recomputed from the 3D view's
# camera scale factor -- Thomas found that right after a curve/fiducial
# node is freshly created, that scale factor can still be stale (left
# over from whatever camera state the previous page ended on), making
# points render far too large until the "recenter 3D view" button is
# pressed and the camera/clipping state gets recalculated. A fixed mm
# size sidesteps that bug entirely, since rendering no longer depends on
# any camera computation -- points are always this physical size
# regardless of zoom or camera state. See base_page.set_absolute_point_size().
#
# Split into two separate constants (originally one shared
# DRAW_POINT_SIZE_MM) because the scutum defect outline is drawn at a much
# finer scale than the pinna outline -- a size that reads well on one felt
# wrong on the other. Tune independently if needed.
SCUTUM_DRAW_POINT_SIZE_MM = 0.3
PINNA_DRAW_POINT_SIZE_MM = 2.0

# ---------------------------------------------------------------------------
# Curvature comparison (core/curvature/ -- ported from the standalone
# Curvature Project v4, now running in-process, no subprocess/venv needed)
# ---------------------------------------------------------------------------
#
# How many candidate harvest sites to spread across the pinna and coarse-score
# against the defect. Higher = more thorough screening but slower.
CURVATURE_NUM_CANDIDATES = 300

# How many of the best coarse-scoring candidates get refined with local ICP
# alignment (the expensive step). Only the top of these ever get shown to the
# surgeon, so this doesn't need to be large.
CURVATURE_TOP_N_REFINE = 15

# Histogram resolution used when comparing shape-index / curvedness / radial-
# distance distributions between the defect and a candidate patch.
CURVATURE_HIST_BINS = 16

# Extra margin applied to the candidate patch radius vs. the defect's own
# geodesic radius, so a candidate patch is generated slightly larger than the
# defect rather than risking being clipped short.
CURVATURE_PATCH_RADIUS_MARGIN = 1.15

# Percentile of the pinna's own curvedness distribution used to set a shared
# curvedness histogram range, so the defect and every candidate are compared
# on the same scale rather than an assumed fixed range.
CURVATURE_CURVEDNESS_PERCENTILE = 95

# Radius (as a multiple of the mesh's own mean edge length) of the ball used
# to estimate per-vertex curvature via trimesh's discrete curvature measures
# (see core/curvature/descriptors.py). Expressed relative to edge length
# rather than a fixed mm value so this adapts automatically to meshes of
# different density/resolution. Validated against a synthetic sphere (known
# analytic curvature) before trusting it on real anatomy -- see that module's
# docstring for details.
CURVATURE_MEASURE_RADIUS_EDGE_MULTIPLIER = 3.0

# Weights combining the individual shape-signature distances into a single
# coarse score (see core/curvature/scoring.py) -- lower score = better match.
# radial_distance is weighted at least as heavily as the curvature terms
# because it's intrinsic/bending-invariant (cartilage bends readily but
# resists stretching), so a candidate with different curvature but a similar
# intrinsic (geodesic) shape may still be a good match.
CURVATURE_WEIGHT_SHAPE_INDEX = 1.0
CURVATURE_WEIGHT_CURVEDNESS = 1.0
CURVATURE_WEIGHT_RADIAL_DISTANCE = 1.5
CURVATURE_WEIGHT_COMPACTNESS = 0.5
CURVATURE_WEIGHT_ASPECT_RATIO = 0.5
CURVATURE_WEIGHT_AREA = 0.25
