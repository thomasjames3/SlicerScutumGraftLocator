"""
roi_crop.py
===========
Turns the 4 surgeon-placed landmarks into a small cropped sub-volume
("region of interest", or ROI) that comfortably contains the ear canal and
nothing else nearby. Everything downstream (thresholding, connected
components, or the trained model in segment_dl.py) only ever looks inside
this crop -- which makes segmentation both faster and much less likely to
accidentally grab a nearby unrelated air pocket or bone structure.

This is a direct adaptation of the truncated-cylinder VOI approach in
Matin-Mann et al. (2025): two tilted planes (defined by the landmark pairs)
bound the canal's length, and a cylinder of a starting diameter bounds its
width, before any intensity-based segmentation happens.
"""

from __future__ import annotations
import numpy as np
import SimpleITK as sitk
from typing import Tuple

from config import INITIAL_ROI_DIAMETER_MM
from core.landmarks import EarCanalLandmarks


def build_roi_mask(
    reference_image: sitk.Image,
    landmarks: EarCanalLandmarks,
    diameter_mm: float = INITIAL_ROI_DIAMETER_MM,
) -> sitk.Image:
    """
    Build a binary mask (same size/spacing as reference_image) marking the
    truncated-cylinder region of interest defined by the 4 landmarks.

    Parameters
    ----------
    reference_image : sitk.Image
        The (resampled) scan this mask should align with.
    landmarks : EarCanalLandmarks
        Must be complete (landmarks.is_complete() == True) before calling.
    diameter_mm : float
        Starting cylinder diameter. Generous on purpose -- it's meant to
        comfortably contain the canal, not tightly bound it. The actual
        canal boundary is found later by thresholding within this ROI.

    Returns
    -------
    sitk.Image
        A UInt8 label image, 1 inside the ROI and 0 outside.
    """
    if not landmarks.is_complete():
        raise ValueError("Cannot build ROI: landmarks are not all placed yet.")

    canal_axis_start = np.array(landmarks.canal_opening)
    canal_axis_end = np.array(landmarks.near_eardrum)
    outer_normal_pt = np.array(landmarks.reference_outer)
    inner_normal_pt = np.array(landmarks.reference_inner)

    axis_vec = canal_axis_end - canal_axis_start
    axis_length = np.linalg.norm(axis_vec)
    axis_unit = axis_vec / axis_length

    # The two bounding planes are defined by a point (the landmark) and a
    # normal direction (from the paired reference landmark toward it).
    outer_plane_point = canal_axis_start
    outer_plane_normal = _unit(canal_axis_start - outer_normal_pt)

    inner_plane_point = canal_axis_end
    inner_plane_normal = _unit(inner_normal_pt - canal_axis_end)

    size = reference_image.GetSize()
    spacing = reference_image.GetSpacing()
    origin = np.array(reference_image.GetOrigin())
    direction = np.array(reference_image.GetDirection()).reshape(3, 3)

    # Build a grid of physical-space coordinates for every voxel. For a
    # typical cropped-region volume this is a manageable amount of memory;
    # if this ever gets called on a full uncropped scan, resample/crop to
    # a coarse bounding box first.
    idx_grid = np.indices(size[::-1]).reshape(3, -1).T[:, ::-1]  # (N, 3) ijk
    physical_coords = origin + (idx_grid * spacing) @ direction.T

    radius = diameter_mm / 2.0

    # Distance from the canal axis line (perpendicular distance), used for
    # the cylinder wall.
    vec_from_start = physical_coords - canal_axis_start
    along_axis = vec_from_start @ axis_unit
    perp_vec = vec_from_start - np.outer(along_axis, axis_unit)
    perp_dist = np.linalg.norm(perp_vec, axis=1)

    inside_cylinder = perp_dist <= radius

    # "Inside" the outer plane means on the same side as the canal
    outer_side = (physical_coords - outer_plane_point) @ outer_plane_normal >= 0
    inner_side = (physical_coords - inner_plane_point) @ inner_plane_normal >= 0

    inside_roi = inside_cylinder & outer_side & inner_side

    mask_array = inside_roi.reshape(size[::-1]).astype(np.uint8)
    mask_image = sitk.GetImageFromArray(mask_array)
    mask_image.CopyInformation(reference_image)
    return mask_image


def crop_to_roi_bounding_box(
    image: sitk.Image, roi_mask: sitk.Image, margin_mm: float = 2.0
) -> sitk.Image:
    """
    Crop `image` down to the bounding box of `roi_mask`, plus a small
    margin. This is a plain rectangular crop (fast, cheap) used purely to
    shrink the volume before the more expensive cylinder-mask math and
    downstream segmentation -- it is NOT the final segmentation boundary.
    """
    stats = sitk.LabelStatisticsImageFilter()
    stats.Execute(image, roi_mask)
    bbox = stats.GetBoundingBox(1)  # (xmin, xmax, ymin, ymax, zmin, zmax) in index space

    margin_vox = [int(round(margin_mm / s)) for s in image.GetSpacing()]

    start = [
        max(bbox[0] - margin_vox[0], 0),
        max(bbox[2] - margin_vox[1], 0),
        max(bbox[4] - margin_vox[2], 0),
    ]
    size = [
        min(bbox[1] - bbox[0] + 2 * margin_vox[0], image.GetSize()[0] - start[0]),
        min(bbox[3] - bbox[2] + 2 * margin_vox[1], image.GetSize()[1] - start[1]),
        min(bbox[5] - bbox[4] + 2 * margin_vox[2], image.GetSize()[2] - start[2]),
    ]

    return sitk.RegionOfInterest(image, size=size, index=start)


def crop_to_physical_bounds(image: sitk.Image, min_point, max_point) -> sitk.Image:
    """
    Cheaply crop `image` to an axis-aligned physical-space box, without
    needing any precomputed mask -- this only transforms the box's 8
    corners through the image's coordinate system (constant-time), unlike
    build_roi_mask()/build_spherical_roi_mask() which evaluate every voxel.

    This exists specifically to shrink a full-resolution scan down to a
    small region *before* calling build_roi_mask() or
    build_spherical_roi_mask() -- calling either of those directly on an
    uncropped, full-resolution scan can try to allocate an array with one
    row per voxel in the entire volume (hundreds of millions of rows for a
    real clinical CT resampled to a fine isotropic spacing), which is what
    the memory error you'd see looks like. Always coarse-crop first with
    this function (or crop_to_landmark_region / crop_to_point_region
    below, which build the box for you), then build the precise mask on
    the now-small cropped result.

    Parameters
    ----------
    image : sitk.Image
        The (typically full-resolution, uncropped) scan.
    min_point, max_point : (x, y, z)
        Opposite corners of the box, in physical (RAS mm) coordinates.

    Returns
    -------
    sitk.Image
        The cropped image.
    """
    min_point = np.asarray(min_point, dtype=float)
    max_point = np.asarray(max_point, dtype=float)

    # Transform all 8 corners (not just the 2 given) through the image's
    # coordinate system, since the image may be rotated relative to the
    # physical axes -- this keeps the crop correct even then, while still
    # only costing 8 lookups rather than one per voxel.
    corners = [
        (x, y, z)
        for x in (min_point[0], max_point[0])
        for y in (min_point[1], max_point[1])
        for z in (min_point[2], max_point[2])
    ]
    corner_indices = np.array(
        [image.TransformPhysicalPointToContinuousIndex(c) for c in corners]
    )

    size = np.array(image.GetSize())
    start = np.clip(np.floor(corner_indices.min(axis=0)), 0, size - 1).astype(int)
    end = np.clip(np.ceil(corner_indices.max(axis=0)), 0, size).astype(int)
    region_size = np.maximum(end - start, 1)

    return sitk.RegionOfInterest(
        image, size=[int(s) for s in region_size], index=[int(s) for s in start]
    )


def crop_to_landmark_region(
    image: sitk.Image, landmarks: EarCanalLandmarks, margin_mm: float = 15.0
) -> sitk.Image:
    """
    Coarse pre-crop for the ear canal: a rectangular box around all 4
    landmark points, expanded generously by `margin_mm` on every side.
    Call this BEFORE build_roi_mask() -- see crop_to_physical_bounds()'s
    docstring for why. The generous default margin is intentional: this
    step only needs to comfortably contain the cylinder ROI that
    build_roi_mask() will compute next, not precisely bound it, so erring
    larger here costs a little extra (still-cheap) computation rather than
    risking clipping off part of the real ROI.
    """
    points = np.array(
        [
            landmarks.canal_opening,
            landmarks.near_eardrum,
            landmarks.reference_outer,
            landmarks.reference_inner,
        ]
    )
    min_point = points.min(axis=0) - margin_mm
    max_point = points.max(axis=0) + margin_mm
    return crop_to_physical_bounds(image, min_point, max_point)


def crop_to_point_region(
    image: sitk.Image, center_point, radius_mm: float, margin_mm: float = 10.0
) -> sitk.Image:
    """
    Coarse pre-crop for the pinna: a box around a single center point,
    sized to the given radius plus a margin. Call this BEFORE
    build_spherical_roi_mask() -- see crop_to_physical_bounds()'s
    docstring for why.
    """
    center = np.array(center_point)
    total_radius = radius_mm + margin_mm
    min_point = center - total_radius
    max_point = center + total_radius
    return crop_to_physical_bounds(image, min_point, max_point)


def _unit(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v)
    if norm < 1e-8:
        raise ValueError(
            "Two landmarks that should define a direction are identical or "
            "nearly identical. Please re-place these points further apart."
        )
    return v / norm


def build_spherical_roi_mask(
    reference_image: sitk.Image,
    center_point: Tuple[float, float, float],
    radius_mm: float,
) -> sitk.Image:
    """
    Build a binary mask (same size/spacing as reference_image) marking a
    simple spherical region of interest around a single point.

    This is the pinna's equivalent of build_roi_mask() above -- much
    simpler since the pinna's Stage A step doesn't need an axis/orientation,
    just a search region to threshold within.

    Parameters
    ----------
    reference_image : sitk.Image
        The (resampled) scan this mask should align with.
    center_point : (x, y, z)
        Physical-space (RAS mm) center of the sphere, e.g.
        PinnaLandmarks.ear_center.
    radius_mm : float
        Sphere radius. See PINNA_ROI_RADIUS_MM in config.py for tuning
        guidance.

    Returns
    -------
    sitk.Image
        A UInt8 label image, 1 inside the sphere and 0 outside.
    """
    center = np.array(center_point)

    size = reference_image.GetSize()
    spacing = reference_image.GetSpacing()
    origin = np.array(reference_image.GetOrigin())
    direction = np.array(reference_image.GetDirection()).reshape(3, 3)

    idx_grid = np.indices(size[::-1]).reshape(3, -1).T[:, ::-1]  # (N, 3) ijk
    physical_coords = origin + (idx_grid * spacing) @ direction.T

    dist_from_center = np.linalg.norm(physical_coords - center, axis=1)
    inside_sphere = dist_from_center <= radius_mm

    mask_array = inside_sphere.reshape(size[::-1]).astype(np.uint8)
    mask_image = sitk.GetImageFromArray(mask_array)
    mask_image.CopyInformation(reference_image)
    return mask_image
