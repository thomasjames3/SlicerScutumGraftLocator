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
import time
import numpy as np
import SimpleITK as sitk
from scipy import ndimage
from scipy.spatial import cKDTree

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
    # TEMPORARY TIMING INSTRUMENTATION (2026-07-29, see CLAUDE.md "Pinna
    # segmentation performance") -- the roi_crop grid-building fix helped
    # but Thomas reports Stage A is still quite slow on this scan's fine
    # native spacing. cropped_image here is the ROI sphere's bounding BOX
    # (not just the sphere), which at this resolution is itself tens of
    # millions of voxels -- every step below runs a full pass over it.
    # These prints (Slicer Python console) exist purely to find out which
    # step actually dominates before guessing at another fix. Remove once
    # the next bottleneck is identified and addressed.
    _t0 = time.time()
    smoothed = sitk.SmoothingRecursiveGaussian(
        cropped_image, sigma=GAUSSIAN_SMOOTHING_SIGMA_MM
    )
    print(f"[pinna timing] Gaussian smoothing: {time.time() - _t0:.2f}s (image size {cropped_image.GetSize()})")

    _t0 = time.time()
    smoothed_array = sitk.GetArrayFromImage(smoothed)
    roi_array = sitk.GetArrayFromImage(roi_mask).astype(bool)
    print(f"[pinna timing] array conversion: {time.time() - _t0:.2f}s")

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
    _t0 = time.time()
    above_threshold = (smoothed_array > threshold) & roi_array
    print(f"[pinna diag] voxels above threshold within ROI: {int(above_threshold.sum())}")
    print(f"[pinna timing] thresholding: {time.time() - _t0:.2f}s")

    _t0 = time.time()
    labeled_array, num_components = _label_6_connected(above_threshold)
    print(f"[pinna diag] connected components found: {num_components}")
    print(f"[pinna timing] connected-component labeling: {time.time() - _t0:.2f}s")

    if num_components == 0:
        # Nothing found -- return an empty label map rather than raising,
        # so the UI can show "no skin surface found, try adjusting the
        # slider or re-checking your ear-center point" instead of crashing.
        empty = sitk.Image(cropped_image.GetSize(), sitk.sitkUInt8)
        empty.CopyInformation(cropped_image)
        return empty

    _t0 = time.time()
    best_component_id = _closest_component_to_point(
        labeled_array, num_components, cropped_image, landmarks.ear_center
    )
    print(f"[pinna timing] component selection: {time.time() - _t0:.2f}s")

    region_mask_array = (labeled_array == best_component_id).astype(np.uint8)
    print(f"[pinna diag] selected component voxel count: {int(region_mask_array.sum())}")

    region_mask = sitk.GetImageFromArray(region_mask_array)
    region_mask.CopyInformation(cropped_image)

    _t0 = time.time()
    region_mask = _remove_boundary_spike(region_mask, cropped_image, landmarks.ear_center)
    print(f"[pinna timing] spike removal (opening): {time.time() - _t0:.2f}s")
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

    NOTE: an attempt (2026-07-27, same day) to restrict this opening to a
    shell near the ROI sphere's own edge -- reasoning that the artifact is
    caused by the *ROI boundary* grazing the scalp, so only material near
    that boundary should ever be at risk -- made things worse: whatever
    distance-from-ROI-edge threshold was tried either let the graze
    artifact's neck survive (undoing the fix) or risked clipping real
    anatomy, and tuning it blind (no local Slicer to test against) wasn't
    converging. Reverted back to this simpler, whole-mask approach, which
    Thomas already confirmed correctly removes the spike with no visible
    anatomy loss (see Known Issues #15 in CLAUDE.md). The circular hole on
    the helix reported afterward turned out to be a separate, unrelated
    bug -- see page_pinna_review.py's `_refresh_mesh_from_segmentation()`
    and its `_segmentation_edited` guard -- not something this function
    needs to solve.

    NOTE (2026-07-29): tried switching this to a distance-transform-based
    Euclidean opening (erode via "distance to background >= radius", dilate
    via "distance to the eroded set <= radius"), reasoning that SimpleITK's
    ball structuring element ought to scale with kernel *volume* and get
    disproportionately expensive at the [9, 9, 8]-voxel radius this scan's
    fine native spacing (0.173x0.173x0.2mm) produces (vs. [3, 3, 3] on a
    coarser ~0.5mm scan). Benchmarked both directly before shipping (per
    this project's synthetic-test-first practice) at that exact radius
    across increasing voxel counts -- the EDT version was NOT faster, it
    was 3-4x SLOWER at real-scan-sized arrays (e.g. 14.1s vs 4.7s at 27M
    voxels) and scaled worse with array size, not better. SimpleITK's
    BinaryMorphologicalOpening (confirmed still using its default Ball
    kernel, which also beat the Box kernel in the same benchmark) is
    already well-optimized here. Reverted -- kept as a documented dead end
    so this isn't re-attempted blind next time pinna segmentation is slow.
    The real fix for that (see roi_crop._build_mask_by_slices) was in the
    ROI-mask grid construction, which -- unlike this filter -- really was
    materializing multi-gigabyte throwaway arrays at this scan's
    resolution.
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
        # part of one, and use its label.
        #
        # FIX (2026-07-30, see CLAUDE.md "Pinna segmentation performance"):
        # this used to call scipy.ndimage.distance_transform_edt(...,
        # return_indices=True) over the WHOLE array -- confirmed via real
        # Slicer console timing to cost 12.91s on a real scan, almost a
        # third of Stage A's entire runtime, to answer a single-point
        # nearest-neighbor query.
        #
        # A first fix (same day) built a cKDTree over every foreground
        # voxel instead -- correct, and faster (12.91s -> 7.30s on a real
        # re-test), but a much smaller win than a synthetic test had
        # predicted (57.7x), because that synthetic test used a SPARSE
        # foreground (a few small blobs) unlike real scans, where ~20-30%
        # of the whole array is foreground (Thomas's log: 12.9M of 41.5M
        # voxels) -- building a cKDTree over that many points isn't free
        # either. A second synthetic test confirmed this directly: at
        # realistic ~20-30% foreground density, the all-foreground cKDTree
        # was only ~2.8x faster than the original full-array EDT, not
        # 57.7x.
        #
        # This is the real fix: for a query point OUTSIDE every component,
        # the nearest foreground voxel can NEVER be a component's interior
        # voxel -- any interior voxel has a same-or-closer surface voxel on
        # the way out, so only SURFACE voxels (foreground adjacent to at
        # least one background voxel) can ever be the answer. For solid,
        # blob-shaped components (real pinna/skin segments, not thin
        # shells), surface voxel count scales with (volume)^(2/3), not
        # volume -- confirmed via synthetic test at Thomas's real
        # foreground density (22 components, ~20-30% foreground): surface
        # voxels were only ~7.7% of total foreground voxels, and building
        # the cKDTree over just those was ~5.5x faster than the
        # all-foreground cKDTree, ~10x faster than the original full-array
        # EDT (12.65s -> 0.82s + 0.44s one-time surface extraction),
        # verified identical results across all trials. A simpler
        # "growing local search box" alternative was also tried and
        # REJECTED: it gambles on the query point happening to be near
        # foreground, and was directly measured to be WORSE than the
        # original full-array EDT (10.14s vs 9.62s) when that gamble
        # didn't pay off -- unlike this surface-voxel approach, which has
        # no such locality dependency and was never slower in any trial.
        foreground = labeled_array != 0
        eroded = ndimage.binary_erosion(foreground, structure=np.ones((3, 3, 3)))
        surface_coords = np.argwhere(foreground & ~eroded)
        tree = cKDTree(surface_coords)
        _, nearest_pos = tree.query([z, y, x])
        nz, ny, nx = surface_coords[nearest_pos]
        best_id = int(labeled_array[nz, ny, nx])

    print(f"[pinna diag] selected component {best_id} via nearest-voxel-to-landmark")
    return best_id
