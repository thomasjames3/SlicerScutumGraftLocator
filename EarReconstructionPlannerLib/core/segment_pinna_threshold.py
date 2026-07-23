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

    # Skin/tissue = above threshold. Restrict to inside the ROI so we don't
    # pick up unrelated tissue (e.g. the opposite side of the head, or an
    # arm/shoulder if the scan's field of view includes it) far from the
    # ear.
    above_threshold = (smoothed_array > threshold) & roi_array

    labeled_array, num_components = _label_6_connected(above_threshold)

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

    region_mask = sitk.GetImageFromArray(region_mask_array)
    region_mask.CopyInformation(cropped_image)
    return region_mask


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
    of the one whose center of mass is closest to the surgeon's ear_center
    point. This is the pinna equivalent of
    segment_threshold._closest_component_to_axis_line() -- same purpose
    (pick the anatomically-relevant blob), simpler geometry (a point
    instead of a line, since the pinna doesn't need an axis).
    """
    center = np.array(center_point)

    spacing = np.array(reference_image.GetSpacing())
    origin = np.array(reference_image.GetOrigin())
    direction = np.array(reference_image.GetDirection()).reshape(3, 3)

    best_id = None
    best_dist = np.inf

    centers_of_mass = ndimage.center_of_mass(
        labeled_array, labeled_array, range(1, num_components + 1)
    )

    for component_id, com_index in zip(range(1, num_components + 1), centers_of_mass):
        # com_index is in (z, y, x) array order; convert to physical (x, y, z)
        ijk = np.array([com_index[2], com_index[1], com_index[0]])
        physical = origin + (ijk * spacing) @ direction.T

        dist = np.linalg.norm(physical - center)
        if dist < best_dist:
            best_dist = dist
            best_id = component_id

    return best_id
