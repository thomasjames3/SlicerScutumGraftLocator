"""
segment_pinna_threshold.py
===========================
Stage A pinna segmentation: finds the outer SKIN surface within a spherical
region of interest around the surgeon's ear-center landmark.

Read this carefully before touching thresholds: this does NOT attempt to
isolate cartilage by intensity. Cartilage-vs-skin contrast on CT is poor
and inconsistent -- there is no reliable published threshold for it, unlike
the ear canal's clean air-vs-bone boundary. Instead, this step finds the
skin-vs-air boundary (excellent contrast, same trick used for the ear
canal's air lumen) within a region around the ear, producing a clean patch
of head-skin surface for the surgeon to draw on. The drawing step (see
core/mesh_isolate.py) is what actually separates "the pinna" from the
surrounding scalp/cheek/neck skin -- this file's job is just to narrow the
search area and produce a clean surface, not to find the pinna's exact
boundary.
"""

from __future__ import annotations
import numpy as np
import SimpleITK as sitk
from scipy import ndimage

from config import (
    SKIN_AIR_THRESHOLD,
    PINNA_ROI_RADIUS_MM,
    GAUSSIAN_SMOOTHING_SIGMA_MM,
    CONNECTED_COMPONENT_CONNECTIVITY,
    PINNA_SPIKE_REMOVAL_RADIUS_MM,
)
from core.pinna_landmarks import PinnaLandmarks


def segment_pinna_region(
    cropped_image: sitk.Image,
    roi_mask: sitk.Image,
    landmarks: PinnaLandmarks,
    threshold: float = SKIN_AIR_THRESHOLD,
) -> sitk.Image:
    """
    Segment the outer skin surface within the cropped ROI around the ear.

    The result is a solid blob of "head near the ear" (skin + everything
    inside it, since we're thresholding skin-vs-air, not doing a thin-shell
    extraction like the ear canal's bone wall) -- NOT yet just the pinna.
    The surgeon's drawn outline (applied afterward, see
    core/mesh_isolate.py) is what narrows this down to just the pinna
    shape. This function's job is only to produce a clean, well-cropped
    surface for them to draw on.

    Parameters
    ----------
    cropped_image : sitk.Image
        The grayscale scan, already cropped to roughly the ROI (see
        roi_crop.crop_to_roi_bounding_box using a mask from
        roi_crop.build_spherical_roi_mask).
    roi_mask : sitk.Image
        Binary sphere mask from roi_crop.build_spherical_roi_mask, same
        geometry as cropped_image.
    landmarks : PinnaLandmarks
        Used to find the "correct" connected component -- the one closest
        to the surgeon's ear_center point -- in case thresholding picks up
        a disconnected piece of skin/tissue elsewhere in the ROI (e.g. an
        edge-of-scan artifact).
    threshold : float
        Intensity threshold separating skin/soft tissue (above) from air
        (below). Exposed to the surgeon as a single slider in the review
        step; this function gets called again each time they move it, so
        it needs to stay fast.

    Returns
    -------
    sitk.Image
        A UInt8 label image (same geometry as cropped_image), 1 = skin
        surface region, 0 = everything else (mainly surrounding air).
    """
    smoothed = sitk.SmoothingRecursiveGaussian(
        cropped_image, sigma=GAUSSIAN_SMOOTHING_SIGMA_MM
    )

    smoothed_array = sitk.GetArrayFromImage(smoothed)
    roi_array = sitk.GetArrayFromImage(roi_mask).astype(bool)

    # TEMPORARY DIAGNOSTICS (see Known Issues #7 in CLAUDE.md) -- prints to
    # the Slicer Python console (View > Python console) so an
    # EmptySegmentationError can be traced back to its actual cause: an
    # empty/misplaced ROI vs. a threshold that's wrong for this scan's
    # intensity calibration. Remove once #7 is resolved.
    roi_voxel_count = int(roi_array.sum())
    if roi_voxel_count > 0:
        intensities_in_roi = smoothed_array[roi_array]
        print(
            f"[pinna diag] ROI voxel count: {roi_voxel_count} | "
            f"intensity in ROI: min={intensities_in_roi.min():.1f} "
            f"max={intensities_in_roi.max():.1f} "
            f"mean={intensities_in_roi.mean():.1f} | threshold={threshold}"
        )
    else:
        print(
            "[pinna diag] ROI voxel count: 0 -- the spherical ROI does not "
            "overlap the cropped image at all. This means ear_center is "
            "landing outside the volume, not a threshold problem."
        )

    # Skin/tissue = above threshold. Restrict to inside the ROI so we don't
    # pick up unrelated tissue (e.g. the opposite side of the head, or an
    # arm/shoulder if the scan's field of view includes it) far from the
    # ear.
    above_threshold = (smoothed_array > threshold) & roi_array
    print(f"[pinna diag] voxels above threshold within ROI: {int(above_threshold.sum())}")

    labeled_array, num_components = _label_6_connected(above_threshold)
    print(f"[pinna diag] connected components found: {num_components}")

    if num_components == 0:
        # Nothing found -- return an empty label map rather than raising,
        # so the UI can show "no skin surface found, try adjusting the
        # slider or re-checking your ear-center point" instead of crashing.
        empty = sitk.Image(cropped_image.GetSize(), sitk.sitkUInt8)
        empty.CopyInformation(cropped_image)
        return empty

    best_component_id = _closest_component_to_point(
        labeled_array, num_components, cropped_image, landmarks.ear_center
    )

    region_mask_array = (labeled_array == best_component_id).astype(np.uint8)
    print(f"[pinna diag] selected component voxel count: {int(region_mask_array.sum())}")

    region_mask = sitk.GetImageFromArray(region_mask_array)
    region_mask.CopyInformation(cropped_image)

    region_mask = _remove_boundary_spike(region_mask, cropped_image, landmarks.ear_center)
    return region_mask


def _remove_boundary_spike(
    region_mask: sitk.Image, reference_image: sitk.Image, ear_center
) -> sitk.Image:
    """
    Strips the thin, spike-like sliver that recurs at the outer edge of the
    pinna region mask -- see PINNA_SPIKE_REMOVAL_RADIUS_MM in config.py for
    the mechanism and why this is safe at this stage. Uses a morphological
    opening sized in physical mm (converted to a per-axis voxel radius from
    this image's own spacing, NOT a fixed voxel count, so it behaves the
    same across scans with different spacing), then re-selects whichever
    resulting piece is physically closest to the surgeon's ear_center
    landmark -- opening can occasionally split the mask into more than one
    piece, and only the one actually touching/near the landmark should
    survive.
    """
    spacing = reference_image.GetSpacing()
    radius_vox = [max(1, int(round(PINNA_SPIKE_REMOVAL_RADIUS_MM / s))) for s in spacing]
    opened = sitk.BinaryMorphologicalOpening(region_mask, radius_vox)

    opened_array = sitk.GetArrayFromImage(opened)
    if not opened_array.any():
        # The opening ate the entire mask (radius too large for this
        # mask's thickness) -- fall back to the un-opened mask rather than
        # returning nothing.
        print("[pinna diag] spike removal opening emptied the mask -- skipped")
        return region_mask

    labeled_array, num_components = _label_6_connected(opened_array)
    if num_components > 1:
        best_id = _closest_component_to_point(
            labeled_array, num_components, reference_image, ear_center
        )
        opened_array = (labeled_array == best_id).astype(np.uint8)

    removed_voxels = int(sitk.GetArrayFromImage(region_mask).sum()) - int(opened_array.sum())
    print(f"[pinna diag] spike removal opening stripped {removed_voxels} voxels")

    result = sitk.GetImageFromArray(opened_array.astype(np.uint8))
    result.CopyInformation(reference_image)
    return result


def _label_6_connected(binary_array: np.ndarray):
    """Connected-component labeling with a 6-neighborhood (face-adjacent
    voxels only). Matches the connectivity convention used elsewhere in
    this project (core/segment_threshold.py) for consistency."""
    structure = ndimage.generate_binary_structure(
        3, CONNECTED_COMPONENT_CONNECTIVITY
    )
    labeled_array, num_components = ndimage.label(binary_array, structure=structure)
    return labeled_array, num_components


def _closest_component_to_point(
    labeled_array: np.ndarray,
    num_components: int,
    reference_image: sitk.Image,
    center_point,
) -> int:
    """
    Of all the disconnected skin/tissue blobs found, return the label ID
    of the one physically nearest to the surgeon's ear_center point --
    nearest-voxel (surface) distance, NOT center-of-mass distance.

    Originally used center-of-mass distance (like
    segment_threshold._closest_component_to_axis_line()'s axis-distance
    approach), but that broke on real, artifact-heavy scans: thresholding
    can fragment the ROI into a large number of components (seen in
    practice: 135, from beam-hardening/noise), and the real skin-surface
    component -- a large, irregular blob -- can easily have a center of
    mass far from the landmark (e.g. it bulges toward the neck/scalp),
    while a small noise speck immediately next to the click can have a
    center of mass much closer. Center-of-mass distance was picking the
    speck; postprocess.remove_small_specks then deleted it entirely
    (below MIN_COMPONENT_VOLUME_MM3), producing a silently empty
    segmentation that only surfaced later as mesh_export's
    EmptySegmentationError -- see Known Issues #7 in CLAUDE.md. Nearest-
    voxel distance instead picks whichever blob the landmark is actually
    touching or closest to, regardless of that blob's overall shape.
    """
    if num_components == 1:
        return 1

    size = reference_image.GetSize()
    ijk_continuous = reference_image.TransformPhysicalPointToContinuousIndex(
        [float(c) for c in center_point]
    )
    ijk = [int(round(c)) for c in ijk_continuous]
    ijk = [max(0, min(ijk[axis], size[axis] - 1)) for axis in range(3)]
    z, y, x = ijk[2], ijk[1], ijk[0]  # array is (z, y, x) order

    if labeled_array[z, y, x] != 0:
        best_id = int(labeled_array[z, y, x])
    else:
        # The landmark's own voxel isn't part of any component (e.g. it
        # landed a hair below threshold) -- find the nearest voxel that IS
        # part of one via a distance transform, and use its label.
        _, nearest_indices = ndimage.distance_transform_edt(
            labeled_array == 0, return_indices=True
        )
        nz, ny, nx = nearest_indices[:, z, y, x]
        best_id = int(labeled_array[nz, ny, nx])

    print(f"[pinna diag] selected component {best_id} via nearest-voxel-to-landmark")
    return best_id
