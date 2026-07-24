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
GAUSSIAN_SMOOTHING_SIGMA_MM = TARGET_VOXEL_SPACING_MM

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
# the exported bone wall boundary.
DEFAULT_BONE_THRESHOLD = 300

# Range the review-step slider is allowed to move the bone threshold within.
BONE_THRESHOLD_ADJUST_RANGE = (100, 700)

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

# Mesh smoothing iterations applied before STL export. Higher = smoother
# but less true to the raw voxel boundary.
MESH_SMOOTHING_ITERATIONS = 15

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
SKIN_THRESHOLD_ADJUST_RANGE = (-600, 0)

# Radius (in mm) of the spherical region of interest built around the
# surgeon's single ear-center landmark. Generous on purpose -- it just
# needs to comfortably contain the whole pinna plus a margin of surrounding
# skin for the surgeon to draw against; it is not the final pinna boundary.
PINNA_ROI_RADIUS_MM = 45.0

# Minimum plausible distance (mm) between the two ears in an adult head,
# used only as a sanity check if both ear-center points are ever provided
# in the same session (e.g. planning bilateral reconstruction).
MIN_INTERAURAL_DISTANCE_MM = 100.0

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
