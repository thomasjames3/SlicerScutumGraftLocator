"""
segment_threshold.py
=====================
Stage A segmentation: threshold-based bone wall extraction, run only inside
the ROI built by roi_crop.py.

This is the "always works, needs no training data" engine. It is what a
brand-new install of this tool uses on day 1, before any cases have been
banked for training a model. See segment_dl.py for how Stage B (once
trained) takes over automatically while keeping this as a fallback.

IMPORTANT: the clinically useful output is the BONY WALL of the ear canal,
not the air-filled lumen. But the air lumen is still segmented first and
used internally as a scaffold, because air-vs-tissue contrast on CT is far
more reliable than bone-vs-soft-tissue contrast. The public entry point for
normal use is segment_bone_wall(); _segment_air_lumen() is an internal
helper kept around for that purpose (and it's also handy on its own if you
ever want to sanity-check the scaffold visually while tuning thresholds).

Directly adapted from the thresholding + connected-component method in
Matin-Mann et al. (2025), which reported a mean Dice score of 0.909 against
manual segmentation using this same approach (their paper segments the
canal lumen directly; we extend it with a bone-wall shell step on top).
"""

from __future__ import annotations
import numpy as np
import SimpleITK as sitk
from scipy import ndimage

from config import (
    DEFAULT_AIR_THRESHOLD,
    DEFAULT_BONE_THRESHOLD,
    BONE_WALL_THICKNESS_MM,
    GAUSSIAN_SMOOTHING_SIGMA_MM,
    CONNECTED_COMPONENT_CONNECTIVITY,
)
from core.landmarks import EarCanalLandmarks


def segment_bone_wall(
    cropped_image: sitk.Image,
    roi_mask: sitk.Image,
    landmarks: EarCanalLandmarks,
    air_threshold: float = DEFAULT_AIR_THRESHOLD,
    bone_threshold: float = DEFAULT_BONE_THRESHOLD,
    wall_thickness_mm: float = BONE_WALL_THICKNESS_MM,
) -> sitk.Image:
    """
    Segment the bony wall of the ear canal within the cropped ROI. This is
    the function the rest of the pipeline (postprocess.py, mesh_export.py)
    should be called with -- it's what produces the final exported mesh.

    How it works:
      1. Find the air-filled lumen (the reliable, high-contrast structure)
         and keep only the piece closest to the surgeon's landmark axis.
      2. Dilate that air mask outward by `wall_thickness_mm` to define a
         thin shell around it -- "the wall lives somewhere in here."
      3. Within that shell only, threshold for high-intensity (bone)
         voxels. Restricting to the shell (rather than thresholding bone
         across the whole ROI) is what prevents the segmentation from
         grabbing unrelated bone elsewhere in the ROI, like the mastoid or
         ossicles.

    Parameters
    ----------
    cropped_image : sitk.Image
        The grayscale scan, already cropped to roughly the ROI (see
        roi_crop.crop_to_roi_bounding_box).
    roi_mask : sitk.Image
        Binary cylinder mask from roi_crop.build_roi_mask, same geometry as
        cropped_image.
    landmarks : EarCanalLandmarks
        Used to find the "correct" air-lumen component -- the one nearest
        the canal_opening-to-near_eardrum line.
    air_threshold : float
        Intensity threshold separating air (below) from everything else.
        Used only to build the internal scaffold.
    bone_threshold : float
        Intensity threshold separating bone (above) from soft tissue
        (below). This is the value that actually determines the exported
        wall boundary. Exposed to the surgeon as the main review-step
        slider; this function gets called again each time they move it, so
        it needs to stay fast.
    wall_thickness_mm : float
        How far outward from the air lumen to search for bone. See
        BONE_WALL_THICKNESS_MM in config.py for tuning guidance.

    Returns
    -------
    sitk.Image
        A UInt8 label image (same geometry as cropped_image), 1 = bone
        wall, 0 = everything else (including the air lumen itself).
    """
    air_mask = _segment_air_lumen(cropped_image, roi_mask, landmarks, air_threshold)

    air_array = sitk.GetArrayFromImage(air_mask).astype(bool)
    if not air_array.any():
        # No lumen found -- nothing to build a wall scaffold from. Return
        # empty rather than raising so the UI can prompt "no canal found,
        # try adjusting the slider or re-checking your landmarks."
        empty = sitk.Image(cropped_image.GetSize(), sitk.sitkUInt8)
        empty.CopyInformation(cropped_image)
        return empty

    spacing = cropped_image.GetSpacing()
    # Dilation radius in voxels, per axis, so the physical dilation stays
    # isotropic in mm even though the scan's own voxel spacing generally
    # isn't (native scans are typically anisotropic, e.g. thicker slices
    # than in-plane resolution -- this per-axis conversion is what keeps
    # the function correct on that native spacing).
    radius_voxels = [
        max(1, int(round(wall_thickness_mm / spacing[i]))) for i in range(3)
    ]

    dilated_mask = sitk.BinaryDilate(air_mask, radius_voxels)
    shell_array = sitk.GetArrayFromImage(dilated_mask).astype(bool) & ~air_array

    smoothed = sitk.SmoothingRecursiveGaussian(
        cropped_image, sigma=GAUSSIAN_SMOOTHING_SIGMA_MM
    )
    smoothed_array = sitk.GetArrayFromImage(smoothed)

    # Bone = above threshold, restricted to the thin shell around the
    # lumen. This is what keeps the result to "just the canal wall" rather
    # than any other bone structure that happens to sit inside the ROI.
    bone_wall_array = (smoothed_array > bone_threshold) & shell_array

    bone_wall_mask = sitk.GetImageFromArray(bone_wall_array.astype(np.uint8))
    bone_wall_mask.CopyInformation(cropped_image)
    return bone_wall_mask


def _segment_air_lumen(
    cropped_image: sitk.Image,
    roi_mask: sitk.Image,
    landmarks: EarCanalLandmarks,
    threshold: float = DEFAULT_AIR_THRESHOLD,
) -> sitk.Image:
    """
    Internal helper: segments the air-filled ear canal lumen within the
    cropped ROI. Used by segment_bone_wall() as a scaffold to locate the
    bony wall. Not intended to be exported directly, but kept as a
    separate function (rather than inlined) since it's also useful on its
    own for visually sanity-checking the scaffold while tuning thresholds
    in config.py.

    Parameters mirror segment_bone_wall() -- see that docstring for
    details on landmarks and threshold.

    Returns
    -------
    sitk.Image
        A UInt8 label image, 1 = air lumen, 0 = everything else.
    """
    smoothed = sitk.SmoothingRecursiveGaussian(
        cropped_image, sigma=GAUSSIAN_SMOOTHING_SIGMA_MM
    )

    smoothed_array = sitk.GetArrayFromImage(smoothed)
    roi_array = sitk.GetArrayFromImage(roi_mask).astype(bool)

    # Air = below threshold. Restrict to inside the ROI so we never pick up
    # air pockets from outside the canal region entirely.
    below_threshold = (smoothed_array < threshold) & roi_array

    labeled_array, num_components = _label_6_connected(below_threshold)

    if num_components == 0:
        empty = sitk.Image(cropped_image.GetSize(), sitk.sitkUInt8)
        empty.CopyInformation(cropped_image)
        return empty

    best_component_id = _closest_component_to_axis_line(
        labeled_array, num_components, cropped_image, landmarks
    )

    lumen_mask_array = (labeled_array == best_component_id).astype(np.uint8)

    lumen_mask = sitk.GetImageFromArray(lumen_mask_array)
    lumen_mask.CopyInformation(cropped_image)
    return lumen_mask


def _label_6_connected(binary_array: np.ndarray):
    """Connected-component labeling with a 6-neighborhood (face-adjacent
    voxels only), matching the conservative connectivity used in the
    reference paper -- less likely to bridge across a thin bone wall than
    26-connectivity would be."""
    structure = ndimage.generate_binary_structure(
        3, CONNECTED_COMPONENT_CONNECTIVITY
    )
    labeled_array, num_components = ndimage.label(binary_array, structure=structure)
    return labeled_array, num_components


def _closest_component_to_axis_line(
    labeled_array: np.ndarray,
    num_components: int,
    reference_image: sitk.Image,
    landmarks: EarCanalLandmarks,
) -> int:
    """
    Of all the disconnected air blobs found, return the label ID of the one
    whose center of mass is closest to the line connecting canal_opening
    and near_eardrum. This is the key trick from the reference paper that
    prevents the segmentation from accidentally grabbing e.g. a piece of
    the middle ear cavity that also happens to fall inside the ROI.
    """
    canal_axis_start = np.array(landmarks.canal_opening)
    canal_axis_end = np.array(landmarks.near_eardrum)
    axis_vec = canal_axis_end - canal_axis_start
    axis_unit = axis_vec / np.linalg.norm(axis_vec)

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

        vec_from_start = physical - canal_axis_start
        along_axis = vec_from_start @ axis_unit
        perp_vec = vec_from_start - along_axis * axis_unit
        perp_dist = np.linalg.norm(perp_vec)

        if perp_dist < best_dist:
            best_dist = perp_dist
            best_id = component_id

    return best_id
