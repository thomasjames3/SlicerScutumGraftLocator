"""
roi_crop.py
===========
Turns the 2 surgeon-placed ear canal landmarks into a small cropped
sub-volume ("region of interest", or ROI) that comfortably contains the
ear canal and nothing else nearby. Everything downstream (thresholding,
connected components, or the trained model in segment_dl.py) only ever
looks inside this crop -- which makes segmentation both faster and much
less likely to accidentally grab a nearby unrelated air pocket or bone
structure.

This is a simplified version of the truncated-cylinder VOI approach in
Matin-Mann et al. (2025). The original design used 2 extra landmarks per
end to let the bounding planes tilt to match true anatomy; that was
dropped (see build_roi_mask()'s docstring) because it required the
surgeon's click to land precisely on one side of a plane, contradicting
the "doesn't need to be precise" instruction and repeatedly producing
bad/empty ROIs from perfectly reasonable clicks. The two bounding planes
are now always perpendicular to the canal_opening->near_eardrum axis,
extended by a fixed margin -- since the ROI only needs to *comfortably
contain* the canal (the real boundary comes from thresholding within it,
not the ROI's shape), this loses nothing in practice.
"""

from __future__ import annotations
import numpy as np
import SimpleITK as sitk
from typing import Tuple

from config import INITIAL_ROI_DIAMETER_MM, ROI_AXIAL_MARGIN_MM
from core.landmarks import EarCanalLandmarks


def points_inside_roi(
    points: np.ndarray,
    landmarks: EarCanalLandmarks,
    diameter_mm: float = INITIAL_ROI_DIAMETER_MM,
    axial_margin_mm: float = ROI_AXIAL_MARGIN_MM,
) -> np.ndarray:
    """
    Boolean test: which of `points` (an (N, 3) array of physical RAS mm
    coordinates) fall inside the same truncated-cylinder ROI
    build_roi_mask() below builds as a voxel mask. Factored out of that
    function so mesh-space callers (e.g. page_scutum_review.py's
    mesh-based finalize, 2026-07-31) can test mesh vertices directly
    against the identical cylinder definition without needing a voxel
    grid at all.
    """
    if not landmarks.is_complete():
        raise ValueError("Cannot test ROI membership: landmarks are not all placed yet.")

    points = np.asarray(points, dtype=float)
    canal_axis_start = np.array(landmarks.canal_opening)
    canal_axis_end = np.array(landmarks.near_eardrum)

    axis_vec = canal_axis_end - canal_axis_start
    axis_length = np.linalg.norm(axis_vec)
    axis_unit = axis_vec / axis_length

    radius = diameter_mm / 2.0

    vec_from_start = points - canal_axis_start
    along_axis = vec_from_start @ axis_unit
    perp_vec = vec_from_start - np.outer(along_axis, axis_unit)
    perp_dist = np.linalg.norm(perp_vec, axis=1)

    inside_cylinder = perp_dist <= radius
    # Bounding planes, perpendicular to the axis, each extended
    # axial_margin_mm past its landmark -- no surgeon-supplied direction
    # involved, so there's no "wrong side" possible.
    outer_side = along_axis >= -axial_margin_mm
    inner_side = along_axis <= axis_length + axial_margin_mm
    return inside_cylinder & outer_side & inner_side


def build_roi_mask(
    reference_image: sitk.Image,
    landmarks: EarCanalLandmarks,
    diameter_mm: float = INITIAL_ROI_DIAMETER_MM,
    axial_margin_mm: float = ROI_AXIAL_MARGIN_MM,
) -> sitk.Image:
    """
    Build a binary mask (same size/spacing as reference_image) marking the
    truncated-cylinder region of interest defined by the 2 landmarks.

    Unlike the original 4-point design, the two bounding planes are not
    surgeon-controlled -- they're derived automatically, perpendicular to
    the canal_opening->near_eardrum axis, each extended outward by
    `axial_margin_mm` past its landmark. This removes any "which side did
    the surgeon click" ambiguity entirely: with only 2 points, there is no
    other side to get wrong.

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
    axial_margin_mm : float
        How far past canal_opening/near_eardrum, along the axis, the two
        bounding planes extend. See ROI_AXIAL_MARGIN_MM in config.py for
        tuning guidance.

    Returns
    -------
    sitk.Image
        A UInt8 label image, 1 inside the ROI and 0 outside.
    """
    if not landmarks.is_complete():
        raise ValueError("Cannot build ROI: landmarks are not all placed yet.")

    def inside_test(physical_coords: np.ndarray) -> np.ndarray:
        return points_inside_roi(physical_coords, landmarks, diameter_mm, axial_margin_mm)

    mask_array = _build_mask_by_slices(reference_image, inside_test)

    if not mask_array.any():
        raise ValueError(
            "No voxels found in the region defined by these landmarks -- "
            "the ROI is empty. Double-check that 'canal opening' and 'near "
            "eardrum' were placed on the actual scan (not outside the "
            "volume) and aren't identical points."
        )

    mask_image = sitk.GetImageFromArray(mask_array)
    mask_image.CopyInformation(reference_image)
    return mask_image


def _build_mask_by_slices(reference_image: sitk.Image, inside_test) -> np.ndarray:
    """
    Shared helper for build_roi_mask()/build_spherical_roi_mask(): evaluates
    `inside_test(physical_coords)` (an (N, 3) array of physical-space points
    -> an (N,) bool array) one z-slice at a time instead of materializing a
    single (nx*ny*nz, 3) array for the whole volume.

    Both callers used to build that full array up front. On a typical
    cropped-region volume at the ~0.3-0.5mm spacing this was originally
    tested against, that's a manageable size -- but on a real scan with
    much finer native spacing (confirmed: 0.173x0.173x0.2mm on one of
    Thomas's scans), the *voxel count* of the same physical-size crop box
    scales as 1/spacing^3, so the same box can balloon to 100M+ voxels,
    meaning several GB each for idx_grid/physical_coords/along_axis/etc --
    almost certainly the dominant cause of a "really slow" pinna
    segmentation on that scan, worse than any morphological-filter cost.
    Looping over z keeps peak memory to O(nx*ny) (one slice) regardless of
    nz, at the cost of a Python loop over nz iterations -- negligible next
    to the per-slice vectorized numpy work. Output is mathematically
    identical to the old whole-volume computation (same physical-coordinate
    math, still handles oblique direction cosines via the full 3x3
    direction matrix).
    """
    nx, ny, nz = reference_image.GetSize()
    spacing = np.array(reference_image.GetSpacing())
    origin = np.array(reference_image.GetOrigin())
    direction = np.array(reference_image.GetDirection()).reshape(3, 3)

    ii, jj = np.meshgrid(np.arange(nx), np.arange(ny), indexing="xy")  # (ny, nx)
    # Physical-coordinate contribution of the x/y indices for one slice --
    # computed once and reused for every z, since it doesn't depend on z.
    xy_phys = (
        origin
        + ii[..., None] * (spacing[0] * direction[:, 0])
        + jj[..., None] * (spacing[1] * direction[:, 1])
    )  # (ny, nx, 3)
    z_axis_vec = spacing[2] * direction[:, 2]  # (3,)

    mask_array = np.zeros((nz, ny, nx), dtype=np.uint8)
    for k in range(nz):
        slice_phys = (xy_phys + k * z_axis_vec).reshape(-1, 3)
        mask_array[k] = inside_test(slice_phys).reshape(ny, nx)

    return mask_array


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


def crop_to_own_bounding_box(label_image: sitk.Image, margin_mm: float) -> sitk.Image:
    """
    Crops `label_image` down to the tight bounding box of its OWN
    foreground (label value 1), plus `margin_mm` on every side.

    Unlike crop_to_roi_bounding_box() above (which crops one image to the
    bounding box of a SEPARATE mask -- e.g. cropping the raw scan to the
    coarse ROI sphere's bounding box, before that ROI has even been
    thresholded), this crops a segmentation result down to just the space
    its own detected tissue actually occupies -- meant to run AFTER
    postprocessing and BEFORE meshing, so morphological smoothing and
    marching_cubes stop paying for the surrounding empty-air padding that
    the coarse ROI crop necessarily carries. See PINNA_TIGHT_CROP_MARGIN_MM
    in config.py for how the margin was chosen.

    Correctness note: this only ever affects HOW MUCH of the (already
    fully-decided) label array gets processed downstream, not WHERE any
    voxel sits physically -- sitk.RegionOfInterest (used internally, via
    crop_to_roi_bounding_box) correctly updates the returned image's own
    origin to account for the crop offset, so code that reads geometry
    from the cropped image itself (e.g. mesh_export's marching_cubes call,
    which builds physical-space vertices from `label_image.GetOrigin()`)
    produces bit-identical physical (RAS mm) positions either way.

    Returns `label_image` UNCHANGED if it has no foreground at all, rather
    than raising -- callers already handle a genuinely empty segmentation
    via mesh_export.EmptySegmentationError downstream; this function isn't
    the right place to introduce a second, different error path for that
    same case.
    """
    if not sitk.GetArrayViewFromImage(label_image).any():
        return label_image
    return crop_to_roi_bounding_box(label_image, label_image, margin_mm=margin_mm)


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
    Coarse pre-crop for the ear canal: a rectangular box around the 2
    landmark points, expanded generously by `margin_mm` on every side.
    Call this BEFORE build_roi_mask() -- see crop_to_physical_bounds()'s
    docstring for why. The generous default margin is intentional: this
    step only needs to comfortably contain the cylinder ROI that
    build_roi_mask() will compute next (including its radius and
    ROI_AXIAL_MARGIN_MM extension past each landmark), not precisely
    bound it, so erring larger here costs a little extra (still-cheap)
    computation rather than risking clipping off part of the real ROI.
    """
    points = np.array([landmarks.canal_opening, landmarks.near_eardrum])
    min_point = points.min(axis=0) - margin_mm
    max_point = points.max(axis=0) + margin_mm
    return crop_to_physical_bounds(image, min_point, max_point)


def crop_to_points_region(
    image: sitk.Image, points, margin_mm: float = 15.0
) -> sitk.Image:
    """
    Coarse pre-crop: a rectangular box around an arbitrary set of physical
    (RAS mm) points, expanded by `margin_mm` on every side. Generalizes
    crop_to_landmark_region() for callers that need the crop to also
    guarantee containment of extra points beyond the 2 canal landmarks
    (e.g. a surgeon-placed calibration seed) -- since every point passed
    in directly defines the box, none of them can ever land outside their
    own crop, unlike cropping to the landmarks alone and then separately
    sampling a seed that may sit further away.
    """
    points = np.array(list(points), dtype=float)
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

    def inside_test(physical_coords: np.ndarray) -> np.ndarray:
        dist_from_center = np.linalg.norm(physical_coords - center, axis=1)
        return dist_from_center <= radius_mm

    # See _build_mask_by_slices()'s docstring -- avoids materializing a
    # full (nx*ny*nz, 3) coordinate array, which is what made this blow up
    # in memory/time on a real scan with fine native spacing.
    mask_array = _build_mask_by_slices(reference_image, inside_test)
    mask_image = sitk.GetImageFromArray(mask_array)
    mask_image.CopyInformation(reference_image)
    return mask_image
