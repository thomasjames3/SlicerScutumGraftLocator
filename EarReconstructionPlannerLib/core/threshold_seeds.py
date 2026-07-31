"""
threshold_seeds.py
===================
Optional per-scan calibration points that pre-fill starting threshold
values for two different pages' segmentation steps, from real intensity
samples on this specific scan instead of one fixed HU pair used for every
patient/scanner.

Points, placed on two different wizard pages:

  - pinna_air_seed + soft_tissue_seed: both placed on the PINNA REVIEW
    page (a click in open air near the ear, and a click on ordinary soft
    tissue near the ear). calibrate_skin_threshold() takes their midpoint
    for that page's own skin/air threshold. soft_tissue_seed is ALSO
    carried forward in WizardState to help calibrate the SCUTUM REVIEW
    page's bone/tissue threshold (calibrate_bone_threshold(), together
    with bone_seed below) -- reused there rather than asking for a third
    soft-tissue click, since ordinary soft tissue near the ear canal and
    near the pinna is the same tissue type.
  - bone_seed: placed on the SCUTUM REVIEW page (a click on solid bone
    near the ear canal). Only feeds calibrate_bone_threshold().

Background: a synthetic 150-patient experiment (run against the real
segment_threshold.segment_bone_wall(), not a reimplementation) found that
deriving the bone threshold from seed-clicks instead of one fixed default
raised mean Dice from 0.599 to 0.650, and the improvement roughly doubled
specifically under simulated scanner HU calibration drift vs. a no-drift
control -- confirming the mechanism is really correcting inter-scan
calibration, not just averaging out noise. See config.py's "seed-based
threshold calibration" section for the full writeup.

Originally a third point (air_seed, a click inside the air-filled canal)
was also placed alongside bone_seed on the scutum page, to derive an
air_threshold the same way. That threshold stopped being used once
page_scutum_review.py was reworked (2026-07-30) to run Slicer's own
Threshold effect directly, rather than the old shell-restricted
segment_threshold.segment_bone_wall() pipeline that needed it -- so THAT
air_seed was pure dead weight (sampled a point, computed a value, and
never used it) and was removed (2026-07-31).

calibrate_skin_threshold() briefly (also 2026-07-31, same day) tried
avoiding a pinna-side air click entirely -- soft_tissue_hu minus a fixed
config constant, on the theory that true open air's HU is close to a
scanner-invariant -1000 by definition of the Hounsfield scale, so a
second click wouldn't add information a constant didn't already have.
**Directly disproven on the first real-Slicer test**: calibrated to -359
HU, but Thomas had already found ~-550 HU necessary for a good result on
that scan by hand -- soft_tissue_hu backed out to -59 (a perfectly
plausible fat/soft-tissue reading), meaning the needed margin below it
was ~491 HU, nowhere near the fixed 300 assumed. Backing out what a
genuine air sample would need to be for midpoint(air_hu, -59) to land at
-550 gives air_hu ~= -1041 -- itself a perfectly plausible real-air
reading, which is what motivated reverting to a real, per-scan air click
rather than guessing a new fixed constant (this project has been burned
more than once -- see CLAUDE.md's sheetness-feature history -- by
retuning a fixed constant against one real scan's feedback and having it
fail the next one; a directly-sampled midpoint, the same mechanism
calibrate_bone_threshold already uses successfully, doesn't have that
problem). Not yet re-confirmed against a real scan.

This is a convenience, not a replacement for either page's threshold
slider: both calibrate_*() functions below only compute a starting value
for those sliders, which stay the actual source of truth and remain
fully surgeon-adjustable.
"""

from __future__ import annotations
from typing import Optional, Tuple

import SimpleITK as sitk

from config import GAUSSIAN_SMOOTHING_SIGMA_MM, MIN_BONE_SOFT_TISSUE_SEPARATION_HU
from core.smoothing import smooth_for_thresholding

Point3D = Tuple[float, float, float]  # (x, y, z) in RAS mm, Slicer's convention


def _sample_hu_at_point(smoothed_image: sitk.Image, point: Point3D) -> float:
    """
    Nearest-voxel intensity lookup at a physical (RAS mm) point.
    TransformPhysicalPointToIndex already rounds to the nearest voxel, so no
    manual rounding is needed. Raises ValueError (surgeon-facing message)
    if the point falls outside the image -- this can happen if a seed was
    placed just outside whatever coarse crop this function is called on.
    """
    index = smoothed_image.TransformPhysicalPointToIndex(point)
    size = smoothed_image.GetSize()
    if any(i < 0 or i >= s for i, s in zip(index, size)):
        raise ValueError(
            "A calibration point fell outside the scan region. Please "
            "go back and re-place it closer to the ear, using this page's "
            "Redo button."
        )
    return float(smoothed_image.GetPixel(index))


def calibrate_bone_threshold(
    image: sitk.Image,
    bone_seed: Point3D,
    soft_tissue_seed: Point3D,
) -> float:
    """
    bone_threshold = midpoint(bone_seed, soft_tissue_seed) -- the
    bone/tissue boundary that determines the exported wall.

    Smooths `image` with the exact same function
    segment_threshold.segment_bone_wall() itself uses before thresholding
    (core/smoothing.py), so the sampled intensities reflect what
    thresholding will actually see -- calibrating against the raw
    (unsmoothed) image would systematically mismatch the values the
    slider is meant to drive.

    Raises ValueError if either seed point falls outside `image` (see
    _sample_hu_at_point) -- meant to be caught by the caller and shown via
    the page's statusLabel, same idiom as roi_crop.build_roi_mask's
    ValueError.
    """
    smoothed = smooth_for_thresholding(image)
    bone_hu = _sample_hu_at_point(smoothed, bone_seed)
    soft_hu = _sample_hu_at_point(smoothed, soft_tissue_seed)
    return (bone_hu + soft_hu) / 2.0


def calibrate_skin_threshold(
    image: sitk.Image,
    air_seed: Point3D,
    soft_tissue_seed: Point3D,
) -> float:
    """
    skin_threshold = midpoint(air_seed, soft_tissue_seed) -- the skin/air
    boundary that determines which voxels count as skin surface.

    Same mechanism as calibrate_bone_threshold() (a real midpoint between
    two directly-sampled points, not a point plus a guessed fixed offset)
    -- see this module's docstring for why a fixed-offset version of this
    function was tried first and reverted after failing its first
    real-Slicer test.

    Smooths with plain Gaussian at GAUSSIAN_SMOOTHING_SIGMA_MM -- matching
    segment_pinna_threshold.segment_pinna_region()'s own smoothing exactly
    (NOT smooth_for_thresholding/CurvatureFlow, which is scutum-pipeline-
    only, see core/smoothing.py) -- for the same reason
    calibrate_bone_threshold must match its own pipeline's smoothing:
    calibrating against a differently-smoothed field would systematically
    mismatch what thresholding will actually see.

    Raises ValueError if either seed point falls outside `image`.
    """
    smoothed = sitk.SmoothingRecursiveGaussian(image, sigma=GAUSSIAN_SMOOTHING_SIGMA_MM)
    air_hu = _sample_hu_at_point(smoothed, air_seed)
    soft_hu = _sample_hu_at_point(smoothed, soft_tissue_seed)
    return (air_hu + soft_hu) / 2.0


def check_bone_soft_tissue_plausibility(
    image: sitk.Image,
    bone_seed: Point3D,
    soft_tissue_seed: Point3D,
) -> Optional[str]:
    """
    Plain-English, advisory-only warning (never raises) if the bone seed's
    sampled intensity isn't meaningfully above the soft-tissue seed's --
    the most likely explanation is the "bone" click actually landed on soft
    tissue. Returns None if everything looks plausible.
    """
    smoothed = smooth_for_thresholding(image)
    bone_hu = _sample_hu_at_point(smoothed, bone_seed)
    soft_hu = _sample_hu_at_point(smoothed, soft_tissue_seed)

    if (bone_hu - soft_hu) < MIN_BONE_SOFT_TISSUE_SEPARATION_HU:
        return (
            "The bone calibration point doesn't look distinctly denser "
            "than the soft-tissue point -- double-check that the 'bone' "
            "click landed on actual bone, not soft tissue."
        )
    return None
