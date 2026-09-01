"""
wall_quality.py
================
Post-segmentation thin-wall check. Zero Slicer dependency, matching the
rest of core/'s "no hard Slicer dependency" rule.

Background: a synthetic 150-patient diagnostic (instrumented run of the
same experiment used to justify core/threshold_seeds.py) found that
catastrophic bone-wall segmentation failures (Dice ~0) are NOT caused by
the internal air-lumen scaffold step failing -- its Dice stayed ~0.90-0.97
even in failing cases, uncorrelated with wall failure (r=-0.15). They're
caused by the TRUE anatomical wall being thin relative to the scan's
effective blur/PSF: partial-volume effect smears a thin wall's intensity
into its neighbors before any threshold -- fixed or seed-calibrated --
can separate it. True wall thickness alone correlated r=+0.83 with final
wall Dice; noise correlated ~0.05.

No threshold choice can fix this failure mode, so this module detects and
flags it after the fact instead, so a confidently-wrong-looking mesh
doesn't get silently trusted. See config.py's "post-segmentation
wall-thickness warning" section for the full writeup and the
MIN_SAFE_WALL_THICKNESS_MM default's justification.

This is advisory only -- check_wall_thickness() returns a plain-English
warning string or None, following the same idiom as
core/landmarks.EarCanalLandmarks.validate(). The caller (page_scutum_
review.py) displays it but never blocks on it.
"""

from __future__ import annotations
from typing import Optional

import numpy as np
import SimpleITK as sitk
from scipy import ndimage

import config


def estimate_local_thickness_mm(
    label_image: sitk.Image,
    max_expected_half_thickness_mm: float = config.BONE_WALL_THICKNESS_MM,
) -> np.ndarray:
    """
    Approximate local wall thickness (mm) at every voxel of `label_image`'s
    foreground (nonzero) mask.

    Starts from a cheap, standard building block: a Euclidean distance
    transform of the mask gives each foreground voxel's distance to the
    nearest background voxel. On a locally slab-like (thin-sheet) structure
    like a bone wall, a voxel sitting on the sheet's medial axis is roughly
    half-thickness away from the nearest background voxel on either face,
    so 2x its EDT value approximates the local thickness there.

    That raw per-voxel value is a poor thickness estimate almost
    everywhere else, though: in any solid region, only a single-voxel-wide
    ridge actually sits on the medial axis -- most voxels are close to one
    face or the other and get a much smaller raw value, even where the
    structure is genuinely thick there. So this propagates each ridge
    voxel's value outward to nearby off-axis voxels via a local maximum
    filter (`max_expected_half_thickness_mm` sets how far outward to look --
    it reuses BONE_WALL_THICKNESS_MM, the existing "how thick could this
    reasonably be" assumption from segment_threshold.py's search shell).
    This cheaply approximates the standard "largest inscribed sphere"
    definition of local thickness (as used e.g. by Hildebrand/Ruegsegger-
    style bone morphometry) without actually fitting spheres -- still an
    approximation, weakest at junctions/mesh ends and wherever the true
    thickness exceeds `max_expected_half_thickness_mm`, and not
    clinical-grade measurement, but enough for a warning signal. One
    specific known limitation from using a fixed-size window rather than
    true sphere-fitting: a genuinely thin, localized patch sitting within
    `max_expected_half_thickness_mm` of a much thicker region can inherit
    that thicker region's value and go unflagged -- a real sphere-fitting
    algorithm wouldn't have this failure mode, since a large sphere
    centered in the thick region can't actually fit inside the thin patch.

    Returns a flat array of thickness estimates, one per foreground voxel
    (empty array if the mask is empty).
    """
    array = sitk.GetArrayFromImage(label_image).astype(bool)  # (z, y, x) order
    if not array.any():
        return np.array([], dtype=float)

    spacing = label_image.GetSpacing()  # (x, y, z) order
    # distance_transform_edt's `sampling` must match the array's own axis
    # order -- (z, y, x) -- same convention mesh_export.py uses for
    # marching_cubes.
    sampling_zyx = (spacing[2], spacing[1], spacing[0])

    edt = ndimage.distance_transform_edt(array, sampling=sampling_zyx)
    raw_thickness = 2.0 * edt

    # Local max filter, window sized (per axis, in the array's z,y,x order)
    # so it comfortably reaches from any off-axis voxel to a medial-axis
    # ridge up to max_expected_half_thickness_mm away.
    radius_voxels_xyz = [
        max(1, int(round(max_expected_half_thickness_mm / spacing[i]))) for i in range(3)
    ]
    filter_size_zyx = tuple(2 * radius_voxels_xyz[i] + 1 for i in (2, 1, 0))
    propagated_thickness = ndimage.maximum_filter(raw_thickness, size=filter_size_zyx)

    return propagated_thickness[array]


def check_wall_thickness(
    label_image: sitk.Image,
    min_thickness_mm: float = config.MIN_SAFE_WALL_THICKNESS_MM,
    percentile: float = 5.0,
) -> Optional[str]:
    """
    Returns a plain-English warning if the segmented wall in `label_image`
    appears too thin, in places, to trust -- or None if it looks fine.

    Uses the `percentile`-th percentile of local thickness (default: 5th),
    not the raw minimum, so a single stray thin voxel at a crop boundary
    or mesh edge doesn't trigger a false warning -- see
    estimate_local_thickness_mm()'s docstring for why edges are the
    approximation's weak point.

    Returns None (not a warning) if the mask is empty -- that case is
    core/mesh_export.EmptySegmentationError's responsibility, not this
    function's.
    """
    thickness_values = estimate_local_thickness_mm(label_image)
    if thickness_values.size == 0:
        return None

    thin_point_mm = float(np.percentile(thickness_values, percentile))
    if thin_point_mm < min_thickness_mm:
        return (
            f"The segmented canal wall appears very thin in places "
            f"(~{thin_point_mm:.2f} mm at its thinnest, below the "
            f"{min_thickness_mm:.1f} mm this tool can reliably resolve). "
            f"This can mean either genuinely thin bone or that scan "
            f"resolution is smoothing the wall away. Please double-check "
            f"this area manually before relying on it."
        )
    return None
