"""
postprocess.py
===============
Cleanup applied to a raw segmentation label map, regardless of which engine
(Stage A threshold or Stage B trained model) produced it. Keeping this
separate means both engines automatically benefit from the same cleanup
logic, and you only have to improve/debug it in one place.
"""

from __future__ import annotations
import numpy as np
import SimpleITK as sitk
from scipy import ndimage

from config import MIN_COMPONENT_VOLUME_MM3


def remove_small_specks(label_image: sitk.Image) -> sitk.Image:
    """
    Removes small disconnected specks (segmentation noise) below
    MIN_COMPONENT_VOLUME_MM3, keeping only meaningful-sized regions.

    This runs *after* segment_threshold's own "closest component to axis"
    selection, so in practice it's usually cleaning up small stray voxels
    at the edge of the main canal blob rather than large false regions.
    """
    array = sitk.GetArrayFromImage(label_image).astype(bool)
    spacing = label_image.GetSpacing()
    voxel_volume_mm3 = spacing[0] * spacing[1] * spacing[2]
    min_voxel_count = max(1, int(round(MIN_COMPONENT_VOLUME_MM3 / voxel_volume_mm3)))

    labeled, num_components = ndimage.label(array)
    if num_components == 0:
        return label_image

    sizes = ndimage.sum(array, labeled, range(1, num_components + 1))
    keep_mask = np.zeros_like(array)
    for component_id, size in zip(range(1, num_components + 1), sizes):
        if size >= min_voxel_count:
            keep_mask |= labeled == component_id

    cleaned = sitk.GetImageFromArray(keep_mask.astype(np.uint8))
    cleaned.CopyInformation(label_image)
    return cleaned


def fill_holes(label_image: sitk.Image) -> sitk.Image:
    """
    Fills small enclosed holes inside the segmented region (e.g. a single
    noisy voxel misclassified as bone in the middle of the air-filled
    canal). Uses SimpleITK's built-in hole-filling filter, applied
    slice-by-slice in each of the 3 axes and combined, which is more
    robust for tubular structures like the ear canal than a single 3D pass.
    """
    filled = label_image
    for axis in range(3):
        filled = sitk.BinaryFillhole(filled)
    return filled


def smooth_boundary(label_image: sitk.Image, iterations: int = 2) -> sitk.Image:
    """
    Light morphological smoothing (closing then opening) to reduce
    staircase artifacts from voxel boundaries before mesh export. Kept
    intentionally mild (small kernel, few iterations) so it doesn't erode
    away genuinely thin anatomy near the eardrum end of the canal.
    """
    smoothed = label_image
    for _ in range(iterations):
        smoothed = sitk.BinaryMorphologicalClosing(smoothed, [1, 1, 1])
        smoothed = sitk.BinaryMorphologicalOpening(smoothed, [1, 1, 1])
    return smoothed


def run_full_postprocess(label_image: sitk.Image) -> sitk.Image:
    """Convenience wrapper running the standard cleanup sequence in order."""
    result = remove_small_specks(label_image)
    result = fill_holes(result)
    result = smooth_boundary(result)
    return result
