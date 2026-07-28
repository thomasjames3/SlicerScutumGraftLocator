"""
threshold_seeds.py
===================
Optional per-scan calibration for the two Stage A thresholds in
core/segment_threshold.py (DEFAULT_AIR_THRESHOLD / DEFAULT_BONE_THRESHOLD).
Kept separate from core/landmarks.py -- same reasoning as
core/pinna_landmarks.py -- since this is a different kind of point (an
intensity sample, not an anatomical axis/center reference) used for a
different purpose.

Background: config.py's air/bone thresholds are one fixed HU pair applied
to every patient/scanner. A synthetic 150-patient experiment (run against
the real segment_threshold.segment_bone_wall(), not a reimplementation)
found that deriving both thresholds instead from 3 quick surgeon
seed-clicks -- one in the air lumen, one on bone, one on ordinary soft
tissue -- raised mean Dice from 0.599 to 0.650, and the improvement roughly
doubled specifically under simulated scanner HU calibration drift vs. a
no-drift control, confirming the mechanism is really correcting inter-scan
calibration, not just averaging out noise. See config.py's "seed-based
threshold calibration" section for the full writeup.

This is a convenience, not a replacement for the review page's sliders:
calibrate_thresholds() below only computes starting values for those
sliders, which stay the actual source of truth and remain fully
surgeon-adjustable.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple

import SimpleITK as sitk

import config

Point3D = Tuple[float, float, float]  # (x, y, z) in RAS mm, Slicer's convention


@dataclass
class ThresholdSeeds:
    """
    3 points the surgeon clicks on the raw scan (before any segmentation
    exists) to calibrate the air/bone thresholds for this specific scan.

    air_seed:
        A point inside the air-filled ear canal lumen.
    bone_seed:
        A point on solid bone near the ear canal.
    soft_tissue_seed:
        A point on ordinary soft tissue near the ear canal -- neither bone
        nor air. This is the shared reference point calibrate_thresholds()
        anchors BOTH boundaries against: the air/tissue split and the
        bone/tissue split.

    Unlike EarCanalLandmarks/PinnaLandmarks, placing these is entirely
    optional -- the review page's sliders already have sensible defaults
    from config.py, so an incomplete ThresholdSeeds is not itself a
    problem (see validate() below).
    """

    air_seed: Optional[Point3D] = None
    bone_seed: Optional[Point3D] = None
    soft_tissue_seed: Optional[Point3D] = None

    def is_complete(self) -> bool:
        return (
            self.air_seed is not None
            and self.bone_seed is not None
            and self.soft_tissue_seed is not None
        )

    def validate(self) -> Optional[str]:
        """
        Plain-English, forgiving sanity check -- same philosophy as
        EarCanalLandmarks.validate()/PinnaLandmarks.validate(), but with a
        much tighter proximity threshold (1mm, not 5mm): these 3 points are
        *expected* to sit close together right at the canal wall by design
        (unlike the canal-axis landmarks, which are 10-25mm apart), so
        reusing the wider threshold would false-positive on entirely normal
        seed placement. Returns None if incomplete -- incompleteness isn't
        itself a warning here, since calibration is optional.
        """
        if not self.is_complete():
            return None

        import math

        points = {
            "air": self.air_seed,
            "bone": self.bone_seed,
            "soft tissue": self.soft_tissue_seed,
        }
        names = list(points.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                if math.dist(points[a], points[b]) < 1.0:
                    return (
                        f"The '{a}' and '{b}' calibration points are almost "
                        "on top of each other. Please double-check these "
                        "points, or use Redo Calibration Points to re-place them."
                    )
        return None


# Plain-language instructions + reference image filenames shown in the
# wizard, one per step -- same shape as core.landmarks.LANDMARK_STEPS /
# core.pinna_landmarks.PINNA_LANDMARK_STEP.
SEED_STEPS = [
    {
        "field": "air_seed",
        "instruction": "Click a point inside the air-filled ear canal (the dark, hollow part).",
        "reference_image": "step1_air_seed.png",
    },
    {
        "field": "bone_seed",
        "instruction": "Click a point on solid bone near the ear canal.",
        "reference_image": "step2_bone_seed.png",
    },
    {
        "field": "soft_tissue_seed",
        "instruction": "Click a point on ordinary soft tissue near the ear canal (not bone, not air).",
        "reference_image": "step3_soft_tissue_seed.png",
    },
]


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
            "re-place it closer to the ear canal, using Redo Calibration Points."
        )
    return float(smoothed_image.GetPixel(index))


def calibrate_thresholds(
    image: sitk.Image,
    seeds: ThresholdSeeds,
    smoothing_sigma_mm: float = config.GAUSSIAN_SMOOTHING_SIGMA_MM,
) -> Tuple[float, float]:
    """
    Derives (air_threshold, bone_threshold) from the 3 seed points, to
    pre-fill the review page's sliders for this specific scan.

    Smooths `image` with the exact same filter/sigma
    segment_threshold.segment_bone_wall() itself uses before thresholding,
    so the sampled intensities reflect what thresholding will actually see
    -- calibrating against the raw (unsmoothed) image would systematically
    mismatch the values the sliders are meant to drive.

    air_threshold = midpoint(air_seed, soft_tissue_seed) -- the air/tissue
    boundary used internally to find the air-lumen scaffold.
    bone_threshold = midpoint(bone_seed, soft_tissue_seed) -- the
    bone/tissue boundary that determines the exported wall.

    Raises ValueError if `seeds` is incomplete, or if any seed point falls
    outside `image` (see _sample_hu_at_point) -- both are meant to be
    caught by the caller and shown via the page's statusLabel, same idiom
    as roi_crop.build_roi_mask's ValueError.
    """
    if not seeds.is_complete():
        raise ValueError("Cannot calibrate thresholds: not all 3 calibration points are placed yet.")

    smoothed = sitk.SmoothingRecursiveGaussian(image, sigma=smoothing_sigma_mm)

    air_hu = _sample_hu_at_point(smoothed, seeds.air_seed)
    bone_hu = _sample_hu_at_point(smoothed, seeds.bone_seed)
    soft_hu = _sample_hu_at_point(smoothed, seeds.soft_tissue_seed)

    air_threshold = (air_hu + soft_hu) / 2.0
    bone_threshold = (bone_hu + soft_hu) / 2.0
    return air_threshold, bone_threshold


def check_seed_plausibility(
    image: sitk.Image,
    seeds: ThresholdSeeds,
    smoothing_sigma_mm: float = config.GAUSSIAN_SMOOTHING_SIGMA_MM,
) -> Optional[str]:
    """
    Plain-English, advisory-only warning (never raises) if the bone seed's
    sampled intensity isn't meaningfully above the soft-tissue seed's --
    the most likely explanation is the "bone" click actually landed on soft
    tissue. Returns None if seeds are incomplete or everything looks
    plausible.
    """
    if not seeds.is_complete():
        return None

    smoothed = sitk.SmoothingRecursiveGaussian(image, sigma=smoothing_sigma_mm)
    bone_hu = _sample_hu_at_point(smoothed, seeds.bone_seed)
    soft_hu = _sample_hu_at_point(smoothed, seeds.soft_tissue_seed)

    if (bone_hu - soft_hu) < config.MIN_BONE_SOFT_TISSUE_SEPARATION_HU:
        return (
            "The bone calibration point doesn't look distinctly denser "
            "than the soft-tissue point -- double-check that the 'bone' "
            "click landed on actual bone, not soft tissue."
        )
    return None
