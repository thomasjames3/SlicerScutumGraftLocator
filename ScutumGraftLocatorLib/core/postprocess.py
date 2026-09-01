"""
postprocess.py
===============
Cleanup applied to a raw segmentation label map, regardless of which engine
(Stage A threshold or Stage B trained model) produced it. Keeping this
separate means both engines automatically benefit from the same cleanup
logic, and you only have to improve/debug it in one place.
"""

from __future__ import annotations
import time
import numpy as np
import SimpleITK as sitk
from scipy import ndimage

from config import MIN_COMPONENT_VOLUME_MM3, TUNNEL_CLOSING_RADIUS_MM


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


def close_small_tunnels(label_image: sitk.Image, radius_mm: float = TUNNEL_CLOSING_RADIUS_MM) -> sitk.Image:
    """
    Seals small tunnels/handles straight through otherwise-solid tissue
    via a morphological closing (dilate then erode), with NO matching
    opening/erosion step -- see TUNNEL_CLOSING_RADIUS_MM in config.py for
    the full mechanism and why this is a genuinely different defect from
    an open boundary hole (fill_holes() can't fix it; a mesh can be fully
    watertight while still having handles). Deliberately closing-only:
    unlike smooth_boundary()'s closing+opening pair, this can only ADD
    material to bridge gaps, never remove/erode existing material, so it
    can't cause the kind of erosion that ate the pinna's helix.
    """
    spacing = label_image.GetSpacing()
    radius_vox = [max(1, int(round(radius_mm / s))) for s in spacing]
    return sitk.BinaryMorphologicalClosing(label_image, radius_vox)


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


def run_full_postprocess(
    label_image: sitk.Image, close_tunnels: bool = False, smooth: bool = True
) -> sitk.Image:
    """Convenience wrapper running the standard cleanup sequence in order.

    close_tunnels defaults to False: close_small_tunnels()'s 2mm closing
    radius is wide enough to bridge (i.e. erase) real anatomical folds on
    the pinna's outer skin surface -- the helix rim, antihelix grooves,
    concha bowl are all on that same ~1-3mm scale, confirmed against a
    synthetic 2mm-wide groove (fully sealed by a 2mm-radius closing). It
    only earns its keep on the pinna's post-isolation mesh, where genus>0
    handles from canal-adjacent anatomy were the actual problem -- see the
    close_tunnels=True call in page_pinna_draw.py's isolate-fallback path.
    Applied at Stage A (before the surgeon has even drawn anything, see
    page_pinna_review.py), it was silently flattening the raw segmentation
    the surgeon draws on, degrading accuracy with no matching benefit
    (2026-07-28 regression report). Scutum's bone-wall shell still opts in
    explicitly (page_scutum_review.py) since that structure is genuinely
    tube/tunnel-shaped and unaffected by this fix.

    smooth defaults to True (unchanged behavior for every caller that
    doesn't pass it): smooth_boundary()'s morphological closing+opening
    exists to clean up noise from an *automated* threshold pass. It
    actively hurts a mask the surgeon already hand-refined via Slicer's
    live Threshold/Paint/Erase effects (page_scutum_review.py) -- rounding
    corners and shifting the boundary by up to its 1-voxel kernel on a
    mask that was already precise, with no noise left to justify it.
    Scutum's finalize step passes smooth=False for this reason (2026-07-31
    regression report: "preview 3d result" visibly degraded a
    good hand-tuned segmentation).
    """
    # TEMPORARY TIMING INSTRUMENTATION (2026-07-29, see CLAUDE.md "Pinna
    # segmentation performance") -- see matching note in
    # segment_pinna_threshold.segment_pinna_region. Remove once the
    # remaining Stage A slowness is tracked down.
    _t0 = time.time()
    result = remove_small_specks(label_image)
    print(f"[pinna timing] remove_small_specks: {time.time() - _t0:.2f}s")

    _t0 = time.time()
    result = fill_holes(result)
    print(f"[pinna timing] fill_holes: {time.time() - _t0:.2f}s")

    if close_tunnels:
        _t0 = time.time()
        result = close_small_tunnels(result)
        print(f"[pinna timing] close_small_tunnels: {time.time() - _t0:.2f}s")

    if smooth:
        _t0 = time.time()
        result = smooth_boundary(result)
        print(f"[pinna timing] smooth_boundary: {time.time() - _t0:.2f}s")
    return result
