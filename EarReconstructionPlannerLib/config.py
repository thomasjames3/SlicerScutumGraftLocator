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

# Upper cap (Hounsfield-Units) used only as the "Maximum" value when
# page_scutum_review.py's "Auto-Calibrate & Segment" button applies a
# calibrated starting threshold via Slicer's own embedded Segment Editor
# Threshold effect (see that page's module docstring for the interactive-
# threshold rework). Deliberately high so the initial 1-click segmentation
# never silently excludes real dense cortical bone above the calibrated
# minimum -- the surgeon can still narrow it from the effect's own Maximum
# slider afterward for a specific case.
SCUTUM_INTERACTIVE_THRESHOLD_MAX_HU = 3000

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
# Hessian-eigenvalue sheet/plate enhancement (core/sheetness.py)
# ---------------------------------------------------------------------------
#
# Attacks two failure modes plain intensity thresholding can't fix even
# after the CurvatureFlow smoothing swap above: (1) a genuinely thin true
# bone wall partial-volumes below bone_threshold and is missed outright --
# the wall's local SHAPE (a thin high-intensity plate) is still present in
# the smoothed field even though its intensity value is gone; (2) the thin
# true air gap between the scutum and malleus still gets blurred above
# bone_threshold in the extreme 1-voxel-wide case even by CurvatureFlow
# (confirmed on a real scan -- see CLAUDE.md's "Still open" note) -- the
# gap's local shape (a thin low-intensity septum between denser tissue) is
# likewise still detectable, and used here to veto a bone classification
# even where blurred intensity crept above threshold. A multiscale
# Hessian-eigenvalue-ratio "sheetness" measure, in the same family as
# Frangi-style vessel/plate enhancement filters used elsewhere in medical
# imaging for thin anatomical structures.

# Dev-only kill switch: False = pure intensity thresholding (pre-sheetness
# behavior), no code changes needed to revert. Set back to True (2026-07-29)
# to test the new self-calibrating noise-gate design (SHEETNESS_GAMMA_
# AUTO_SCALE) -- see CLAUDE.md's "Ear-canal/temporal-bone segmentation
# research" section: this passed a cross-scale synthetic portability test
# the three previous fixed-gamma attempts never did, but is still
# unconfirmed on a real scan. Flip back to False if it still doesn't help.
ENABLE_SHEET_ENHANCEMENT = True

# Gaussian-derivative sigma sheetness is evaluated at, expressed as
# MULTIPLES OF THE SCAN'S OWN NATIVE VOXEL SPACING (max dimension) rather
# than a fixed absolute mm list -- see segment_threshold.segment_bone_wall()
# for where this gets converted to actual mm scales per call
# (scale_mm = multiplier * max(cropped_image.GetSpacing())). This matters
# because page_dicom_load.py deliberately does NOT resample scans to
# TARGET_VOXEL_SPACING_MM (0.3mm) -- the live pipeline runs on each scan's
# own native spacing, which varies case to case (confirmed: 0.5mm on one
# real scan Thomas tested, as fine as 0.25mm on others). A FIXED absolute-
# mm scale set (the original design, e.g. 0.3/0.6/1.2mm) becomes
# increasingly sub-voxel (sigma_voxels < 1) on coarser native spacing --
# a poorly-conditioned, noise-AMPLIFYING regime for a Gaussian-derivative
# filter, not just a "slightly less precise" one. Confirmed directly: a
# synthetic noise stress test that gamma=15-20 fully suppressed at 0.3mm
# spacing needed gamma=100+ to suppress at 0.5mm spacing with the SAME
# absolute scales -- i.e. the earlier gamma calibration silently stopped
# transferring the moment native spacing changed, without any error or
# warning. Multipliers chosen so sigma_voxels = multiplier *
# max(spacing)/spacing[i] >= multiplier in every axis regardless of a
# scan's actual (possibly anisotropic) spacing -- i.e. never sub-voxel,
# by construction. NEEDS FURTHER VALIDATION: only tested at 0.3mm and
# 0.5mm native spacing so far. SHEETNESS_GAMMA_AUTO_SCALE below replaces
# what used to be a fixed gamma value coupled to these specific
# multipliers -- being self-calibrating, it SHOULD be far less coupled to
# this choice than the old fixed gamma was, but that itself is unproven;
# re-verify together if a scan with very different (e.g. 0.25mm, or >1mm)
# spacing shows problems.
SHEET_ENHANCEMENT_SCALE_MULTIPLIERS = (1.2, 2.4, 4.0)

# Eigenvalue-ratio discriminant constants (see core/sheetness.py's
# _sheet_response) -- alpha controls how strictly sheet-like (vs
# tube-like) the geometry must look; beta controls how strictly planar
# (vs blob-like). Left at the commonly-used starting default (0.5 each) --
# NEEDS SYNTHETIC VALIDATION on the analytic sheet/tube/blob test.
SHEETNESS_ALPHA = 0.5
SHEETNESS_BETA = 0.5

# Background/noise suppression constant -- keeps the filter from
# hallucinating sheet structure in flat, noisy regions with no real
# structure. REDESIGNED 2026-07-29 (was a fixed absolute
# SHEETNESS_NOISE_SUPPRESSION_GAMMA, first 18.0 then 100.0) after THREE
# rounds of real-scan A/B each found the previous fixed value silently
# stopped working the moment scan characteristics (native spacing, then
# HU scale) changed, with no error or warning -- a fixed absolute
# noise-gate constant fundamentally can't be scan-independent, since a
# Gaussian-derivative filter's raw response magnitude depends on the
# scan's own spacing and intensity/contrast scale.
#
# Adapted from the published Krcah et al. bone-sheetness filter (used for
# femur segmentation, 0.98 Dice, explicitly designed for "invariance to
# density calibration" -- see
# https://github.com/ypauchard/ITK-KrcahSheetnessImageFilter,
# AutomaticSheetnessParameterEstimationImageFilter/
# KrcahSheetnessFeatureGenerator): instead of a fixed number, the actual
# noise-gate value is now computed automatically at call time, per scale,
# as SHEETNESS_GAMMA_AUTO_SCALE * median(S) -- where S is this module's
# own structure-strength term (see core/sheetness.py's _sheet_response).
# Two design choices matter here, both found by direct testing rather
# than assumed:
#   1. MEDIAN, not mean. A first version used mean(S) over the whole
#      cropped ROI and found it barely gated anything at all, regardless
#      of this scale factor's value -- the mean gets dragged toward
#      whatever fraction of the crop is plain empty background, which can
#      wash the statistic down to near-meaningless. Median is far more
#      robust to that.
#   2. Restricted to `calibration_mask` (segment_threshold.py passes its
#      `shell_array`), not the whole array -- the statistic should
#      reflect the region the bone/no-bone decision is actually made in.
# Since a scan with more noise, coarser spacing, or higher intensity
# contrast will itself have a higher median(S) in that region, the
# resulting noise-gate value scales up or down automatically with the
# scan, instead of needing a fresh hand-picked constant every time. 2.0
# is validated on one synthetic reconstruction of Thomas's real scan
# (0.5mm spacing, bone_threshold=760): full gap-veto separation, full
# noise suppression back to the no-sheetness baseline, AND near-total
# thin-wall recall (99.75%, better than any fixed-gamma value found in
# earlier rounds) all held simultaneously at this value. NEEDS REAL-SCAN
# VALIDATION -- still only tested on synthetic data; if this under- or
# over-suppresses noise on a real scan, retune this ratio, but first
# re-check whether the self-calibration mechanism itself transfers before
# assuming it needs a different value per scan (that would defeat its
# purpose).
SHEETNESS_GAMMA_AUTO_SCALE = 2.0

# Minimum bright-sheetness response (0-1) to OR-boost a voxel into the
# bone classification despite smoothed intensity below bone_threshold --
# targets thin-wall dropout. Validated in the same pipeline-level test as
# gamma above: at 0.5 (this default), a deliberately 1-voxel-thin wall
# region's recall went from 84.6% (no sheetness) to 97-100% (with
# sheetness, gamma in the validated [15,20] window), with no measurable
# drop in the full-thickness "normal" wall region's own recall (~98% in
# both cases). NEEDS REAL-SCAN VALIDATION.
BRIGHT_SHEETNESS_THRESHOLD = 0.5

# Minimum dark-sheetness response (0-1) to AND-veto a voxel out of the
# bone classification despite smoothed intensity above bone_threshold --
# targets the scutum/malleus gap-bridging failure. Lowered from an
# initial 0.5 guess: the pipeline-level test found 0.5 only reduced the
# gap's false-bone fraction from 98% to 50% -- a real improvement but not
# enough to actually separate the two sides (still 1 connected component,
# i.e. still bridged). 0.1 achieved a full, clean topological separation
# (2 connected components) in that same test, with gamma in the [15,20]
# window above and BRIGHT_SHEETNESS_THRESHOLD=0.5. Also requires feeding
# sheetness the RAW (pre-CurvatureFlow) intensity rather than the
# CurvatureFlow-smoothed field -- see segment_threshold.segment_bone_wall()'s
# comment on this: the same test found the gap veto never achieved true
# separation on the smoothed field at ANY threshold tried, only on the
# raw field. NEEDS REAL-SCAN VALIDATION.
DARK_SHEETNESS_THRESHOLD = 0.1

# Intensity floor/ceiling gating the sheetness OR-boost/AND-veto -- added
# after real-Slicer testing (2026-07-29) reported "large areas of soft
# tissue" being misclassified as bone, worse than plain thresholding
# alone. Root cause: bright_sheetness's OR-boost above had NO floor on
# absolute intensity -- any voxel with a strong enough local sheet-like
# SHAPE got promoted to bone regardless of how far its actual HU value
# was from anything bone-plausible. Real soft tissue is full of genuine
# thin, locally-flat anatomical interfaces (fascial planes, muscle septa,
# vessel walls, organ capsules) that are geometrically indistinguishable
# from a partial-volumed bone wall in pure Hessian-eigenvalue shape terms
# -- no amount of SHEETNESS_GAMMA_AUTO_SCALE retuning can fix this,
# since it's not a noise problem, it's that shape alone genuinely cannot
# tell "thin bone" from "thin fascia" without also looking at intensity.
# These two constants make that check explicit and load-bearing: a voxel
# can only be OR-boosted into bone if its own intensity is already within
# BRIGHT_SHEETNESS_INTENSITY_MARGIN_HU of bone_threshold (i.e. plausibly a
# partial-volumed wall, not generic soft tissue), and only AND-vetoed out
# of bone if its intensity is within DARK_SHEETNESS_INTENSITY_MARGIN_HU
# above bone_threshold (plausibly a blurred air/soft-tissue gap, not
# unrelated dense bone elsewhere). This is a hard, categorical guarantee
# (any voxel further than the margin from bone_threshold can NEVER be
# touched by sheetness, regardless of shape response), not a statistical
# one like gamma -- deliberately chosen over further gamma tuning because
# repeated synthetic attempts to reproduce the reported failure (noisy
# tissue at the decision boundary, periodic low-contrast fascia septa, a
# clean sharp tissue-tissue step, and a noisy version of that same step,
# all at intensities far from bone_threshold) all stayed at 0% false
# positive even under the CURRENT gamma=18 -- meaning whatever real CT
# characteristic is actually triggering this wasn't reproduced by any of
# these synthetic models, so this fix does not rely on having correctly
# modeled the real mechanism, only on the categorical intensity-plausibility
# argument above.
#
# BRIGHT_SHEETNESS_INTENSITY_MARGIN_HU widened 150->300 (2026-07-29) after
# Thomas's seed calibration on a real scan gave bone_threshold=760 (well
# above the 100 this was first picked against) with real cortical bone
# reading 1000-1400 -- at the old 150 margin, a wall dimmed by real
# CurvatureFlow blur (which may pull a thin wall down further than this
# feature's own synthetic model showed, since that model was calibrated
# at a different absolute scale/spacing -- see SHEETNESS_NOISE_
# SUPPRESSION_GAMMA's comment for the spacing side of this same lesson)
# could plausibly fall outside eligibility even though it's genuinely
# bone, not soft tissue. 300 keeps the eligible floor (bone_threshold-300)
# comfortably above ordinary soft tissue's typical range (well below 0 HU
# for fat, rarely above ~80 HU for muscle/gland) while giving real
# partial-volume blur more room than the original guess. NEEDS REAL-SCAN
# VALIDATION -- if soft tissue is still getting promoted, tighten this;
# if thin walls are still missing, this may need to go even higher (there
# is no clean synthetic way to pin this down further without knowing how
# far this specific scan's CurvatureFlow pass actually pulls a genuine
# thin wall's intensity down, which isn't something Thomas has reported
# yet).
BRIGHT_SHEETNESS_INTENSITY_MARGIN_HU = 300.0
# Unchanged -- no evidence this side needs adjustment: the reported
# scutum/malleus gap's raw HU (-1100) sits so far below any plausible
# bone_threshold+margin ceiling that this was never the binding
# constraint on that failure.
DARK_SHEETNESS_INTENSITY_MARGIN_HU = 150.0

# ---------------------------------------------------------------------------
# Stage A: seed-based threshold calibration (optional convenience)
# ---------------------------------------------------------------------------
#
# DEFAULT_BONE_THRESHOLD above is one fixed HU value used for every
# patient/scanner. A synthetic 150-patient experiment (run against the
# real segment_threshold.segment_bone_wall() function, varying per-patient
# HU calibration drift, noise, blur, and anatomy) found that deriving it
# instead from 2 quick surgeon seed-clicks (bone / general soft tissue --
# see core/threshold_seeds.py) raised mean Dice from 0.599 to 0.650, and
# the improvement roughly doubled specifically under simulated scanner HU
# drift vs. a no-drift control -- confirming this really corrects
# inter-scan calibration drift, not just averages out noise. This is
# optional: the review page's slider remains the actual source of truth,
# and seed calibration only pre-fills its starting value.
#
# The bone/soft-tissue calibration points are placed on two different
# pages: the soft-tissue point is placed on the PINNA REVIEW page (it
# doubles as one half of the calibration input for that page's own
# skin/air threshold -- see core/threshold_seeds.calibrate_skin_
# threshold(), which pairs it with a separate air point also placed
# there), and carried forward to also calibrate the bone threshold here
# on the SCUTUM REVIEW page, where the bone point is placed. One
# soft-tissue click, reused by both pages, instead of asking for it
# twice -- see core/threshold_seeds.py's module docstring for the full
# reasoning, including why a THIRD point (an air-lumen click, alongside
# the bone click) that used to also be placed here was removed 2026-07-31
# (page_scutum_review.py's Segment-Editor rework, 2026-07-30, made the
# air_threshold it fed genuinely unused -- sampled and discarded every
# time) -- not to be confused with the separate air point now placed on
# the pinna review page instead, which IS used.

# Minimum plausible difference (in Hounsfield-Unit-like intensity) between
# the bone seed and soft-tissue seed's sampled values. If the difference is
# smaller than this, one of the two clicks probably landed in the wrong
# place (most likely the "bone" click actually landed on soft tissue) --
# core/threshold_seeds.check_bone_soft_tissue_plausibility() uses this to show an
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

# Scutum-specific overrides, passed to mesh_export.label_map_to_mesh() only
# by page_scutum_review.py's finalize step. That mask comes from the
# surgeon's own live Threshold/Paint/Erase edits (already precise, not raw
# automated-threshold noise), so the full 15-iteration Laplacian default was
# measurably rounding off detail the surgeon had just dialed in (2026-07-31
# regression report: "preview 3d result" visibly degraded a good hand-tuned
# segmentation).
#
# History (all 2026-07-31, same day):
# 1. Dropped iterations to 2 (matching smooth_boundary()'s own
#    "intentionally mild" count elsewhere in this file) -- confirmed on a
#    real scan to preserve shape much better, but too blocky (raw
#    marching-cubes voxel-grid terracing, barely smoothed).
# 2. Switched to smoothing_method="taubin" (alternating shrink/inflate
#    passes, the standard fix for staircase-vs-shrinkage) at 8 iterations,
#    based on a synthetic hollow-shell-with-thin-ridge test showing
#    Taubin's behavior was more stable across iteration counts than plain
#    Laplacian's. **Confirmed WORSE on a real scan** -- still blocky AND
#    now a visibly wrong shape. Both Laplacian and Taubin operate on mesh
#    VERTICES after marching_cubes has already snapped the surface to the
#    voxel grid -- moving vertices around after the fact is fundamentally
#    the wrong tool to recover sub-voxel positioning; it can only trade
#    blockiness against shape distortion on the same knob, never fix both.
# 3. Replaced vertex-space smoothing with mask_blur_sigma_mm (see below) --
#    a structurally different approach that blurs the MASK before
#    marching_cubes instead of the mesh after. A synthetic test (same
#    hollow-shell-with-ridge geometry) found this beats every vertex-space
#    method (plain Laplacian, Taubin, Humphrey) tried, on BOTH staircase
#    removal AND real-detail preservation simultaneously -- not just a
#    different tradeoff point on the same curve. iterations dropped to 0
#    (fully disabled) so no vertex-space shape risk remains; the mask blur
#    alone handled the synthetic staircase about as well as blur+light-
#    Taubin combined did, so there's little left for vertex smoothing to
#    usefully add. Not yet real-Slicer re-tested.
SCUTUM_MESH_SMOOTHING_ITERATIONS = 0

# Sigma (physical mm) for the pre-marching-cubes mask blur described above
# (mesh_export.label_map_to_mesh(mask_blur_sigma_mm=...)). 0.6mm was the
# synthetic sweet spot for a 2mm-thick wall at 0.4mm native spacing (~1.5x
# spacing) -- clearly better than 0.4mm (undersmoothed, staircase_rms
# 0.061mm) and clearly worse than 0.8mm+ (oversmoothed: staircase_rms rose
# again to 0.073mm at 0.8mm and 0.136mm at 1.0mm as the wall's two
# surfaces started blurring into each other, and real-ridge retention
# dropped below 1.0 -- genuine erosion). Real scans vary in wall thickness
# and native spacing, so this may need adjusting if a particular scan's
# wall is thinner than ~3x this sigma. Not yet real-Slicer tested.
SCUTUM_MESH_MASK_BLUR_SIGMA_MM = 0.6

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

# Target triangle edge length (mm) for mesh_export.decimate_to_target_resolution(),
# applied only to the pinna pipeline (segment_pinna_threshold's skin-surface
# blob) after marching_cubes, never to the scutum bone-wall mesh.
#
# Root cause this addresses (2026-07-30): unlike the ear canal ROI (a thin
# tube, small surface area regardless of spacing), PINNA_ROI_RADIUS_MM's 45mm
# sphere covers a much larger patch of skin, and marching_cubes' vertex count
# scales with (surface area) / (native voxel spacing)^2. Since
# page_dicom_load.py deliberately does NOT resample to a fixed spacing (see
# TARGET_VOXEL_SPACING_MM's own docstring), a scan with fine native spacing
# (e.g. 0.173mm, vs. a more typical ~0.5mm) produces a pinna mesh with many
# times more vertices/faces for the "same" anatomical shape -- confirmed via
# synthetic test (scratchpad, not committed): an 8.4x face-count increase
# going from 0.5mm to 0.173mm spacing on the same synthetic blob. This
# directly explains both the pinna-specific Stage A slowness and the
# draw/isolate step's slowness (core/mesh_isolate.py's networkx-based flood
# fill scales poorly with vertex count) -- the canal pipeline never hits this
# because its mesh stays small regardless of spacing.
#
# Set equal to TARGET_VOXEL_SPACING_MM (0.3mm) -- not because that resampling
# actually happens, but because that's the density scale the rest of this
# pipeline (mesh_isolate.py, draw-step point sizes, etc.) was implicitly
# built and tested around historically, on scans that happened to be near
# that native spacing. decimate_to_target_resolution() only ever reduces
# density (no-ops if the mesh is already coarser than this), so a
# coarse-native scan's pinna mesh is unaffected.
PINNA_MESH_TARGET_EDGE_MM = TARGET_VOXEL_SPACING_MM

# Margin (mm) kept around the pinna segmentation's own tight bounding box
# in roi_crop.crop_to_own_bounding_box(), applied right after postprocessing
# and before meshing (see core/postprocess.py's smooth_boundary() and
# mesh_export.label_map_to_mesh() call sites in page_pinna_review.py /
# page_pinna_draw.py).
#
# Root cause this addresses (2026-07-30, see CLAUDE.md "Pinna segmentation
# performance"): both smooth_boundary() and label_map_to_mesh()'s
# marching_cubes ran on the FULL ROI-sphere bounding box (~41.5M voxels on
# a real scan) even though the actual segmented tissue only occupies a
# fraction of it -- confirmed via real Slicer timing to cost 18-24s
# combined, unaffected by mesh_export.decimate_to_target_resolution() (that
# only shrinks the mesh AFTER these steps already ran on the full box).
# Cropping down to the segmentation's own footprint first removes that
# wasted surrounding-air computation with no change to the final mesh --
# same voxels processed either way.
#
# This margin has to cover TWO different needs, not just one:
# 1. Processing safety: postprocess.smooth_boundary()'s morphological
#    closing/opening only ever reaches 1 voxel from the existing boundary
#    per iteration, and marching_cubes needs a little guaranteed
#    background around the true tissue so the mesh doesn't get an
#    artificial flat "cut" face right at the cropped array's edge. This
#    alone would only need a few mm.
# 2. Segment Editor headroom: the SAME cropped mask is what gets pushed to
#    Slicer as the segmentation node the surgeon can hand-edit (see
#    page_pinna_review.py's _on_run_clicked) -- unlike the scutum's bone
#    wall (a thin, well-defined shell), the pinna's skin-surface
#    segmentation can plausibly miss a bit of real material right at its
#    detected edge (e.g. component selection or spike removal excluding a
#    genuine sliver of skin). If the crop is too tight, the surgeon has no
#    room left in the pushed volume to paint that material back in via
#    Segment Editor -- a real usability regression, not just a
#    theoretical one. This is the larger of the two needs and is why this
#    default is generous (15mm) rather than the few mm #1 alone would need.
PINNA_TIGHT_CROP_MARGIN_MM = 15.0

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

# Optional on-demand ruler (vtkMRMLMarkupsLineNode) on the scutum outline
# page -- a 2-point line the surgeon can toggle on to get a real physical
# distance reading on the bone-wall surface, as a scale reference while
# tracing the defect outline. Colored distinctly (blue) from the outline
# curve's own default Markups color (yellow/green) so the two tools can't
# be confused for one another in the 3D view.
SCUTUM_RULER_POINT_SIZE_MM = 0.3
SCUTUM_RULER_COLOR = (0.0, 0.6, 1.0)

# Harvest-site locator points on the curvature page (page_curvature.py).
# Started at 3.0 but Thomas found that too large on the heatmap -- halved.
HARVEST_SITE_POINT_SIZE_MM = 1.5

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
