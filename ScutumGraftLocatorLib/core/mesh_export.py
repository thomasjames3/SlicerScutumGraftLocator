"""
mesh_export.py
================
Converts a final, approved label map into a smoothed surface mesh and saves
it as STL (or PLY), ready to be picked up by Curvature Project v4.

Uses trimesh + scikit-image's marching_cubes, matching the mesh library
already used in the mesh-comparison project so the two codebases share the
same mesh representation and don't need any format-conversion glue.
"""

from __future__ import annotations
import os
import numpy as np
import SimpleITK as sitk
import trimesh
from scipy import ndimage
from skimage import measure

from config import (
    MESH_SMOOTHING_ITERATIONS,
    SUBVOXEL_MESH_BAND_MM,
    SUBVOXEL_MESH_SAFETY_MARGIN_HU,
    PINNA_MESH_TARGET_EDGE_MM,
    DECIMATION_FRAGMENT_MIN_TRIANGLES,
)
from core import io_utils
from core.smoothing import smooth_for_thresholding

# TEMPORARY (2026-08-22, remove once the decimation-topology fix below is
# confirmed on a real scan) -- prints once at import time so it's obvious
# in the Slicer Python console whether a freshly-restarted Slicer actually
# picked up this file's current contents, same check used for the
# sheetness feature's stale-code scare in CLAUDE.md.
print(f"[mesh_export diag] loaded from {__file__} -- decimation-fragment-repair fix present")


class EmptySegmentationError(ValueError):
    """Raised when a label map has no (or no meaningful) foreground to mesh.

    marching_cubes requires level=0.5 to fall strictly within the volume's
    min/max, which only fails when the label map is uniformly 0 or 1 --
    i.e. segmentation found nothing (or everything). Callers should catch
    this and prompt the surgeon to adjust thresholds/landmarks rather than
    letting skimage's cryptic ValueError surface.
    """


def label_map_to_mesh(
    label_image: sitk.Image,
    smoothing_iterations: int = MESH_SMOOTHING_ITERATIONS,
    smoothing_method: str = "laplacian",
    mask_blur_sigma_mm: float = 0.0,
    max_anisotropy_ratio: float = 0.0,
) -> trimesh.Trimesh:
    """
    Runs marching cubes on a binary label map to produce a surface mesh in
    physical (mm) coordinates, then applies light smoothing.

    Parameters
    ----------
    label_image : sitk.Image
        The final, surgeon-approved segmentation (UInt8, 1 = canal).
    smoothing_iterations : int
        Passed through to the shared smoothing step (see
        _verts_faces_to_trimesh). Defaults to config.MESH_SMOOTHING_ITERATIONS
        (unchanged behavior for existing callers) -- override with a lower
        value for a mask that's already precise (e.g. hand-refined via
        Slicer's Segment Editor), where 15 iterations measurably rounds
        off detail the surgeon just dialed in rather than cleaning up
        automated-threshold noise.
    smoothing_method : str
        "laplacian" (default, unchanged behavior) or "taubin". Both move
        mesh VERTICES after marching_cubes has already snapped the surface
        to the voxel grid -- confirmed on a real scan (2026-07-31) that
        this is the wrong tool for de-blocking an already-precise
        hand-refined mask: enough iterations to visibly remove terracing
        also measurably distorted the true shape. Prefer
        mask_blur_sigma_mm below for that case; these vertex-space methods
        are still the default (unchanged) for masks that came from raw
        automated thresholding, where some rounding is an acceptable
        cleanup rather than a precision loss.
    mask_blur_sigma_mm : float
        If > 0, blurs the binary MASK itself (SimpleITK Gaussian, physical
        mm sigma, applied BEFORE marching_cubes) instead of smoothing mesh
        vertices afterward. marching_cubes then finds where the blurred,
        continuous field crosses level=0.5, which can land anywhere within
        a voxel rather than only ever snapping to the grid -- genuine
        sub-voxel positioning, the same idea label_map_to_mesh_subvoxel()
        uses, but without needing a known single threshold value, so it
        works on a mask with Paint/Erase edits on top of a threshold.
        Operates only on already-decided mask material (not the original
        intensity field), so unlike label_map_to_mesh_subvoxel() it can't
        reopen anything postprocess.py's morphological steps closed.
        Confirmed via a synthetic hollow-shell-with-thin-ridge test
        (scratchpad, not committed) to beat every vertex-smoothing method
        tried (Laplacian, Taubin, Humphrey) on BOTH staircase removal and
        real-detail preservation simultaneously, at sigma roughly 1-1.5x
        the voxel spacing -- too little (below ~1x spacing) barely helps
        staircase, too much (above ~2x spacing) starts eroding real
        detail and can even make terracing worse by interacting with
        nearby surfaces (a 2mm-thick wall's two sides start blurring into
        each other). See config.SCUTUM_MESH_MASK_BLUR_SIGMA_MM. Default
        0.0 (disabled) preserves existing behavior for other callers.
    max_anisotropy_ratio : float
        If > 0, and the label image's coarsest-axis spacing is more than
        this many times its finest-axis spacing, upsamples the coarse
        axis/axes (linear interpolation on the mask cast to float,
        re-thresholded at 0.5 -- the standard sub-voxel-accurate way to
        resample a binary mask, same idea as mask_blur_sigma_mm's
        continuous-field approach) before marching_cubes ever sees it.

        Root cause this addresses (confirmed on a real scan, 2026-08-22):
        marching_cubes can tear a genuinely single-connected voxel mask
        into a non-watertight, multi-component mesh on a severely
        anisotropic grid -- confirmed directly on a real pinna scan at
        0.39/0.39/2.5mm spacing (~6.4:1 Z:XY ratio): all 3 Stage A
        voxel-space checkpoints (segment_pinna_region incl. spike
        removal, postprocess, tight-crop) showed a single connected
        component, but the mesh coming out of THIS function was
        watertight=False with 2 components -- i.e. the tear happens here,
        not upstream. A second real scan at a near-isotropic 0.43/0.43/
        0.5mm spacing (~1.16:1) gave normal results, matching the
        well-known general behavior of marching-cubes-family algorithms
        on badly anisotropic grids. Every scan tested before this one was
        near-isotropic (0.173-0.5mm on every axis), which is why this
        never surfaced earlier. Default 0.0 (disabled) preserves existing
        behavior for every caller that doesn't opt in -- see
        config.MESH_MAX_ANISOTROPY_RATIO for the pinna pipeline's value.
        No-ops (returns the mask unresampled) if already within the
        ratio, so a normal near-isotropic scan is untouched.

    Returns
    -------
    trimesh.Trimesh
        A watertight-ish surface mesh in the scan's physical coordinate
        space (same space Slicer displays everything in), so it lines up
        correctly if loaded alongside the original scan or other meshes
        from the same patient.
    """
    if max_anisotropy_ratio > 0:
        label_image = io_utils.resample_to_bounded_anisotropy(
            label_image, max_anisotropy_ratio, is_label=True
        )

    array = sitk.GetArrayFromImage(label_image)  # (z, y, x) order
    spacing = label_image.GetSpacing()  # (x, y, z) order

    if array.min() == array.max():
        # Uniform label map (almost always all-zero in practice) -- no
        # surface for marching_cubes to find. Raise a clear, catchable
        # error instead of letting skimage's "Surface level must be within
        # volume data range" ValueError surface to the surgeon. Checked on
        # the raw (pre-blur) array -- blurring can only change this if the
        # mask was already all-0/all-1, which this check already catches.
        raise EmptySegmentationError(
            "The segmentation is empty -- no bone wall was found. Try "
            "adjusting the air/bone threshold sliders, or go back and "
            "double-check the landmark placement."
        )

    if mask_blur_sigma_mm > 0:
        blurred_image = sitk.SmoothingRecursiveGaussian(
            sitk.Cast(label_image, sitk.sitkFloat32), sigma=mask_blur_sigma_mm
        )
        mesh_array = sitk.GetArrayFromImage(blurred_image)
    else:
        mesh_array = array

    # marching_cubes expects the spacing tuple in the same axis order as
    # the array it's given, i.e. (z, y, x).
    array_spacing = (spacing[2], spacing[1], spacing[0])

    verts, faces, normals, _ = measure.marching_cubes(
        mesh_array, level=0.5, spacing=array_spacing, allow_degenerate=False
    )

    return _verts_faces_to_trimesh(
        verts, faces, label_image, smoothing_iterations, smoothing_method
    )


def label_map_to_mesh_subvoxel(
    cropped_image: sitk.Image,
    label_image: sitk.Image,
    raw_threshold_mask: sitk.Image,
    threshold_value: float,
    band_radius_mm: float = SUBVOXEL_MESH_BAND_MM,
    intensity_override_mask: sitk.Image = None,
) -> trimesh.Trimesh:
    """
    Like label_map_to_mesh(), but extracts the surface from the underlying
    continuous grayscale field instead of the already-binarized mask, so
    the mesh boundary isn't snapped to the voxel grid.

    Only meaningful when `label_image` actually came from thresholding
    `cropped_image` at `threshold_value` (e.g. segment_threshold.py's
    output) -- NOT for a mask a surgeon hand-painted in Segment Editor,
    where there is no single isovalue the boundary corresponds to. Callers
    should keep using plain label_map_to_mesh() for hand-edited
    segmentations (see page_scutum_review.py's _refresh_mesh_from_
    segmentation() for that distinction).

    Why this helps: label_map_to_mesh()'s marching_cubes runs on the
    already-binarized 0/1 array at level=0.5, which can only ever place a
    vertex on the voxel grid's face/edge midpoints -- any information
    about where the true (continuous, partial-volume-blended) intensity
    actually crossed the threshold within a voxel is already gone by the
    time the mask exists. Small/thin structures are exactly where this
    matters most: a wall only 1-2 voxels thick has very little grid
    resolution to snap to in the first place.

    How: builds a signed-distance-weighted blend of `cropped_image`'s true
    smoothed intensity (kept close to the mask boundary, where the
    original threshold decision actually lives) and a safely-clamped
    constant (far from the boundary, on both sides). Near the boundary,
    marching_cubes sees the real underlying field and can interpolate a
    genuine sub-voxel crossing point; far away, the clamp guarantees no
    spurious extra surface component gets picked up from some other
    same-HU structure incidentally sitting in this (already fairly tight,
    but not empty) crop -- segment_threshold.py's own shell-restriction
    docstring notes the mastoid and ossicles as real examples of exactly
    that kind of nearby, same-density, unrelated material.

    Why `raw_threshold_mask` matters, and isn't optional: `label_image` is
    the *postprocessed* mask (postprocess.run_full_postprocess -- speck
    removal, hole filling, and for the scutum wall specifically, tunnel
    closing). Those morphological steps intentionally add or remove
    material in places the raw threshold crossing does NOT support --
    that's the whole point of e.g. close_small_tunnels(), which bridges a
    genuine gap in the true intensity field to fix a topological handle
    (see CLAUDE.md's "Isolate Patch / mesh topology saga", an 8-round
    debugging history for exactly this class of defect). If this function
    blended toward the true smoothed intensity everywhere near
    `label_image`'s boundary, it would partially undo those fixes: a
    postprocessing-closed tunnel is, by construction, a thin bridge whose
    true intensity is below threshold throughout, so trusting the true
    field there would place no surface across it, silently reopening the
    handle it was closed to fix. So the blend is instead restricted to
    voxels where `raw_threshold_mask` and `label_image` AGREE -- i.e.
    only where postprocessing didn't touch the classification. Everywhere
    postprocessing changed something, this function falls back to a hard
    clamp on `label_image`'s side, matching plain label_map_to_mesh()'s
    blocky-but-correct behavior there.

    Parameters
    ----------
    cropped_image : sitk.Image
        The same (unsmoothed) cropped grayscale image that was fed into
        segment_threshold.segment_bone_wall() (or segment_dl.segment())
        to produce `label_image` -- same geometry, not yet smoothed (this
        function does its own smoothing, matching segment_bone_wall's,
        so the sampled field is the same one thresholding actually saw).
    label_image : sitk.Image
        The final, already-postprocessed binary label map -- what
        actually gets exported.
    raw_threshold_mask : sitk.Image
        The same segmentation's output BEFORE postprocessing (i.e.
        segment_dl.segment()'s / segment_threshold.segment_bone_wall()'s
        direct return value) -- same geometry as `label_image`. Used only
        to identify which voxels postprocessing left unchanged.
    threshold_value : float
        The actual bone_threshold used to produce `label_image` -- the
        isovalue marching_cubes extracts.
    band_radius_mm : float
        How far from the mask boundary (on both sides) to trust the real
        smoothed intensity before blending toward the safety clamp. See
        config.SUBVOXEL_MESH_BAND_MM for tuning guidance.
    intensity_override_mask : sitk.Image, optional
        Same geometry as `label_image`. Marks voxels where
        core/sheetness.py's Hessian-eigenvalue shape analysis overrode the
        pure intensity>threshold_value decision (config.
        ENABLE_SHEET_ENHANCEMENT) -- see
        segment_threshold.segment_bone_wall()'s docstring. This is a
        SEPARATE category from postprocessing changes: `raw_threshold_mask`
        already tells this function where postprocessing changed
        something, but a sheetness override happens BEFORE postprocessing
        even runs, so `postprocess_unchanged` alone can't see it. Left as
        None (the default), a bright-sheetness-boosted thin-wall voxel
        would have its true intensity blended in even though that
        intensity never actually crossed `threshold_value` -- silently
        losing the material sheetness added, since marching_cubes finds no
        real isovalue crossing there. Symmetrically, a dark-sheetness-
        vetoed gap voxel would have its true (above-threshold) intensity
        trusted near the boundary -- silently placing a surface back
        across the gap sheetness existed to preserve. Wherever this mask
        is True, `weight` is hard-clamped to 1.0 exactly like
        `postprocess_unchanged`'s existing gate, so the blend can never
        undo a sheetness decision either.
    """
    mask_array = sitk.GetArrayFromImage(label_image).astype(bool)  # (z, y, x)

    if not mask_array.any() or mask_array.all():
        raise EmptySegmentationError(
            "The segmentation is empty -- no bone wall was found. Try "
            "adjusting the air/bone threshold sliders, or go back and "
            "double-check the landmark placement."
        )

    raw_mask_array = sitk.GetArrayFromImage(raw_threshold_mask).astype(bool)
    postprocess_unchanged = mask_array == raw_mask_array

    smoothed = smooth_for_thresholding(cropped_image)
    smoothed_array = sitk.GetArrayFromImage(smoothed).astype(np.float64)

    spacing = label_image.GetSpacing()  # (x, y, z) order
    sampling_zyx = (spacing[2], spacing[1], spacing[0])

    # Signed distance to the mask boundary (positive inside, negative
    # outside) -- same double-EDT construction used in
    # core/wall_quality.py. weight is 0 right at the boundary (use the
    # true smoothed value there) and 1 a band_radius_mm away or beyond
    # (use the safety-clamped constant instead) -- but only where
    # postprocessing left this voxel's classification unchanged; anywhere
    # postprocessing DID change something, weight is forced to 1 (hard
    # clamp on label_image's own decision) regardless of distance, so
    # deliberate morphological fixes like tunnel-closing can't be
    # partially undone by trusting a true intensity that was never the
    # reason that material is there. See this function's docstring.
    signed_dist_mm = ndimage.distance_transform_edt(
        mask_array, sampling=sampling_zyx
    ) - ndimage.distance_transform_edt(~mask_array, sampling=sampling_zyx)
    weight = np.clip(np.abs(signed_dist_mm) / band_radius_mm, 0.0, 1.0)
    weight = np.where(postprocess_unchanged, weight, 1.0)
    if intensity_override_mask is not None:
        override_array = sitk.GetArrayFromImage(intensity_override_mask).astype(bool)
        weight = np.where(override_array, 1.0, weight)
    clamp_target = np.where(
        signed_dist_mm > 0,
        threshold_value + SUBVOXEL_MESH_SAFETY_MARGIN_HU,
        threshold_value - SUBVOXEL_MESH_SAFETY_MARGIN_HU,
    )
    blended_array = (1.0 - weight) * smoothed_array + weight * clamp_target

    verts, faces, normals, _ = measure.marching_cubes(
        blended_array, level=threshold_value, spacing=sampling_zyx, allow_degenerate=False
    )

    return _verts_faces_to_trimesh(verts, faces, label_image)


def crop_mesh_to_vertex_mask(mesh: trimesh.Trimesh, keep_vertex_mask: np.ndarray) -> trimesh.Trimesh:
    """
    Keeps only faces whose 3 vertices are ALL inside `keep_vertex_mask`,
    then drops the now-unreferenced vertices. A hard boolean crop -- same
    all-or-nothing semantics as the voxel-space `label_array & roi_array`
    intersection used elsewhere in this pipeline, no boundary
    interpolation either way. Mesh-space counterpart to
    roi_crop.crop_to_roi_bounding_box() + the ROI mask intersection, for
    callers working directly with a mesh instead of a label image (see
    roi_crop.points_inside_roi() for the matching per-vertex ROI test).
    """
    face_mask = keep_vertex_mask[mesh.faces].all(axis=1)
    cropped = mesh.copy()
    cropped.update_faces(face_mask)
    cropped.remove_unreferenced_vertices()
    return cropped


def select_mesh_component_nearest_axis(
    mesh: trimesh.Trimesh, axis_start, axis_end, min_area_mm2: float = 0.0
) -> trimesh.Trimesh:
    """
    Splits `mesh` into its connected components and returns the one whose
    vertices are, on average, closest to the line through axis_start/
    axis_end. Mesh-space counterpart to
    segment_threshold._closest_component_to_axis_line() (same purpose via
    ndimage center-of-mass on a labeled voxel array instead): whole-
    surface thresholding can pick up unrelated material (e.g. ossicles, a
    sliver of adjacent skull) now that there's no shell restriction --
    keep only the piece that's actually the canal wall.

    Uses a plain vertex mean rather than trimesh's own `.centroid`
    property (which is area-weighted) -- this only needs to pick the
    right blob, not compute a true center of mass, and a plain mean keeps
    the behavior easy to reason about/verify synthetically.

    `min_area_mm2` (2026-08-31, HYPOTHESIS -- added in response to a real
    scan producing a "successful" but visually empty finalize; not yet
    confirmed this was the actual cause): components below this area are
    excluded from the nearest-axis comparison entirely, falling back to
    the full unfiltered component list (i.e. old behavior) only if EVERY
    component is below the floor -- so a genuinely tiny scan/ROI still
    gets a result rather than an empty-selection error. Without this, a tiny debris fragment sitting coincidentally
    close to the axis line can beat the real, larger-but-not-perfectly-
    centered bone wall on pure average-vertex-distance -- a real risk on
    this page specifically, since "Auto-Calibrate & Segment" thresholds
    the WHOLE volume (no shell restriction) and this mesh-based finalize
    path runs no postprocess/fill_holes cleanup before this selection
    (see page_scutum_review.py's module docstring, "EXPERIMENTAL" note).
    A hard vertex-mask crop (crop_mesh_to_vertex_mask) immediately before
    this call can also itself shatter one connected surface into several
    small shards right at the crop boundary, giving this failure mode
    more chances to fire than the old voxel-space equivalent (which had
    the benefit of a shell-restricted threshold and never saw this kind
    of post-hoc mesh-boundary fragmentation). Default 0.0 preserves the
    old unconditional-nearest behavior for any other caller.
    """
    components = mesh.split(only_watertight=False)
    if len(components) == 0:
        raise EmptySegmentationError(
            "No surface found inside the region of interest after cropping."
        )

    print(
        f"[mesh_export diag] select_mesh_component_nearest_axis: "
        f"{len(components)} component(s), areas(mm2)="
        f"{sorted([round(c.area, 2) for c in components], reverse=True)}, "
        f"min_area_mm2={min_area_mm2}"
    )

    candidates = [c for c in components if c.area >= min_area_mm2]
    if not candidates:
        candidates = components

    axis_start = np.asarray(axis_start, dtype=float)
    axis_end = np.asarray(axis_end, dtype=float)
    axis_vec = axis_end - axis_start
    axis_unit = axis_vec / np.linalg.norm(axis_vec)

    best_component = None
    best_dist = np.inf
    for component in candidates:
        vec = component.vertices.mean(axis=0) - axis_start
        along = vec @ axis_unit
        perp = vec - along * axis_unit
        dist = np.linalg.norm(perp)
        if dist < best_dist:
            best_dist = dist
            best_component = component

    print(
        f"[mesh_export diag] select_mesh_component_nearest_axis: chose "
        f"area={best_component.area:.2f}mm2, dist_from_axis={best_dist:.2f}mm"
    )
    return best_component


def _verts_faces_to_trimesh(
    verts: np.ndarray,
    faces: np.ndarray,
    label_image: sitk.Image,
    smoothing_iterations: int = MESH_SMOOTHING_ITERATIONS,
    smoothing_method: str = "laplacian",
) -> trimesh.Trimesh:
    """
    Shared tail for label_map_to_mesh()/label_map_to_mesh_subvoxel(): both
    marching_cubes calls above return vertices in (z, y, x) voxel-spacing
    units; this converts to physical (x, y, z) coordinates and applies the
    same hole-filling + smoothing to either result.
    """
    # marching_cubes returns vertices in (z, y, x) order; convert to
    # physical (x, y, z) using the image's origin/direction, matching how
    # Slicer and the rest of this pipeline represent points.
    origin = np.array(label_image.GetOrigin())
    direction = np.array(label_image.GetDirection()).reshape(3, 3)
    verts_xyz = verts[:, ::-1]  # (z, y, x) -> (x, y, z) order swap
    verts_physical = origin + verts_xyz @ direction.T

    mesh = trimesh.Trimesh(vertices=verts_physical, faces=faces, process=True)

    # FIX (2026-08-26, see CLAUDE.md "Pinna mesh: disconnected component
    # after marching_cubes on a single-component voxel mask") -- confirmed
    # on a real scan (0.39mm isotropic after the anisotropy-resample fix)
    # that marching_cubes can still split a mask INTO A DISCONNECTED MESH
    # even when every voxel-space diagnostic upstream (segment_pinna_region,
    # postprocess.run_full_postprocess, crop_to_own_bounding_box) confirms
    # exactly 1 connected component (6-connectivity) at every checkpoint --
    # most likely a razor-thin single-voxel bridge that marching_cubes
    # meshes correctly (skimage's default 'lewiner' method is documented to
    # guarantee topologically correct results) but this constructor's own
    # process=True vertex-merge/degenerate-face cleanup then severs. Since
    # the voxel mask is already PROVEN single-component at this point, any
    # split found here is necessarily a meshing artifact, not real separate
    # anatomy -- unlike _repair_decimation_fragments() below (which only
    # drops fragments below a small size threshold, tuned for genuine
    # decimation debris), it's safe to always keep just the single largest
    # piece here regardless of the dropped piece's size. This also has to
    # happen unconditionally, not only inside decimate_to_target_
    # resolution()'s active-decimation branch (the only place that
    # previously did any component-dropping) -- a real scan showed that
    # branch taking its no-op path (mesh already coarser than target),
    # silently skipping the repair entirely and shipping a visibly broken
    # (holey + a floating orphan chunk) mesh to the surgeon. Runs BEFORE
    # fill_holes() below so hole-filling isn't wasted on a piece about to
    # be dropped.
    components = mesh.split(only_watertight=False)
    if len(components) > 1:
        components_by_area = sorted(components, key=lambda c: c.area, reverse=True)
        print(
            f"[mesh_export diag] marching_cubes produced {len(components)} "
            f"disconnected mesh components from a mask already confirmed "
            f"single-component in voxel space -- keeping the largest "
            f"(area={components_by_area[0].area:.2f}mm2), dropping "
            f"{[round(c.area, 2) for c in components_by_area[1:]]}mm2"
        )
        mesh = components_by_area[0]

    # Close small holes/perforations in the mesh surface. These have been
    # a long-standing, previously-tolerated artifact on real scans (per
    # Thomas: the pre-cutout "ball of tissue" mesh has often had small
    # holes, never deemed significant since the surgeon's drawn outline
    # just routes around them) -- but confirmed 2026-07-27 that a mesh
    # with genuine open holes isn't topologically simple, and can defeat
    # core/mesh_isolate.py's "a closed drawn loop separates the surface
    # into two pieces" assumption regardless of where the loop is drawn
    # (see Known Issues #18 in CLAUDE.md). This is purely additive --
    # trimesh.repair.fill_holes() patches gaps with new triangulated
    # faces rather than removing or eroding any existing material, so
    # unlike postprocess.py's volumetric morphological operations, it
    # can't cause the kind of erosion that ate the pinna's helix (Known
    # Issues #15 follow-up). Not guaranteed to close every hole --
    # trimesh's fan-triangulation can fail on large or non-convex
    # boundary loops (confirmed: works reliably for a single-face-sized
    # gap, only partially closes a 3-face gap in a standalone test) -- so
    # isolate_surface_patch's LoopDoesNotSeparateError + fallback-retry
    # logic stays in place as a backstop for whatever slips through.
    trimesh.repair.fill_holes(mesh)

    if smoothing_iterations > 0:
        if smoothing_method == "taubin":
            # Alternating shrink/inflate passes -- removes the same
            # high-frequency marching-cubes terracing as Laplacian without
            # its low-frequency shrink/round-off bias. See
            # label_map_to_mesh()'s docstring for why this is used for the
            # scutum bone wall specifically.
            trimesh.smoothing.filter_taubin(mesh, iterations=smoothing_iterations)
        else:
            trimesh.smoothing.filter_laplacian(
                mesh, iterations=smoothing_iterations
            )

    return mesh


def decimate_to_target_resolution(
    mesh: trimesh.Trimesh, target_edge_mm: float = PINNA_MESH_TARGET_EDGE_MM
) -> trimesh.Trimesh:
    """
    Simplifies `mesh` toward roughly `target_edge_mm`-sized triangles via
    quadric-error decimation (trimesh.simplify_quadric_decimation, backed by
    the fast-simplification package) -- collapses the vertex pairs that
    distort the surface's shape least first, so curvature-rich areas (e.g.
    the helix) keep more detail than flat ones (e.g. the cheek) for the same
    final vertex count.

    Pinna-pipeline only -- see PINNA_MESH_TARGET_EDGE_MM's docstring in
    config.py for why: the pinna's much larger ROI surface area (vs. the
    canal's thin-tube ROI) is what makes its mesh balloon at fine native
    scan spacing in the first place, dragging down both Stage A meshing and
    the draw/isolate step (core/mesh_isolate.py's networkx graph). Never
    applied to the scutum bone-wall mesh, which relies on
    label_map_to_mesh_subvoxel()'s sub-voxel boundary precision -- decimating
    afterward would blur exactly the boundary-placement improvement that
    function exists to add.

    No-ops (returns `mesh` unchanged) if `mesh` is already at or coarser
    than the target density -- this only ever reduces detail, never adds
    it, so a scan with coarse-enough native spacing that marching_cubes
    never produced excessive geometry is left untouched.

    Repairs topology damage after decimating (see _repair_decimation_
    fragments docstring) -- confirmed via a synthetic pinna-like test
    (2026-08-22, a thin curled ridge like a helix fold, at fine native
    spacing) that fast_simplification's quadric decimation can carve tiny
    disconnected slivers off thin/high-curvature regions, leaving the
    mesh non-watertight with a real gap where the fold used to be
    continuous -- this is what Thomas reported as pinna results with
    "many holes/chunks missing" despite an unchanged scan and threshold
    (see CLAUDE.md "Pinna segmentation performance" for the full story).
    The original 2026-07-30 validation only checked vertex-distance
    accuracy on a simple rounded blob, which never exercises this failure
    mode.
    """
    # TEMPORARY (2026-08-22, remove with the diag print above once confirmed
    # on a real scan) -- real before/after numbers from an actual run, not
    # just a synthetic test, are what's needed to confirm/refute this fix.
    components = mesh.split(only_watertight=False)
    print(
        f"[pinna diag] decimate_to_target_resolution() called: "
        f"verts={len(mesh.vertices)} faces={len(mesh.faces)} "
        f"watertight={mesh.is_watertight} "
        f"components={len(components)}"
    )
    if len(components) > 1:
        # TEMPORARY (2026-08-22, remove with the other [pinna diag] prints)
        # -- added after the anisotropy-resample fix fired on a real scan
        # but still left the mesh non-watertight/multi-component. Need the
        # per-component breakdown to tell apart "one real lobe + tiny
        # decimation-style debris" (safe to drop, same class of bug as
        # _repair_decimation_fragments already fixes) from "two comparably
        # -sized real chunks" (a genuine tear needing a different fix) --
        # see CLAUDE.md "Pinna mesh decimation topology bug".
        for i, component in enumerate(components):
            print(
                f"[pinna diag]   component {i}: verts={len(component.vertices)} "
                f"faces={len(component.faces)} area={component.area:.2f}mm2 "
                f"watertight={component.is_watertight} "
                f"bounds={component.bounds.tolist()}"
            )
    triangle_area_mm2 = (3 ** 0.5 / 4) * target_edge_mm ** 2
    target_face_count = max(4, int(mesh.area / triangle_area_mm2))
    if target_face_count >= len(mesh.faces):
        print("[pinna diag] decimate_to_target_resolution() no-op (mesh already coarser than target)")
        return mesh
    decimated = mesh.simplify_quadric_decimation(face_count=target_face_count)
    print(
        f"[pinna diag] after simplify_quadric_decimation: "
        f"verts={len(decimated.vertices)} faces={len(decimated.faces)} "
        f"watertight={decimated.is_watertight} "
        f"components={len(decimated.split(only_watertight=False))}"
    )
    repaired = _repair_decimation_fragments(decimated, triangle_area_mm2)
    print(
        f"[pinna diag] after _repair_decimation_fragments: "
        f"verts={len(repaired.vertices)} faces={len(repaired.faces)} "
        f"watertight={repaired.is_watertight} "
        f"components={len(repaired.split(only_watertight=False))}"
    )
    return repaired


def _repair_decimation_fragments(mesh: trimesh.Trimesh, triangle_area_mm2: float) -> trimesh.Trimesh:
    """
    Drops components too small to be real anatomy at this mesh's own
    target density, then fills whatever hole that leaves behind.

    The input mesh going into decimate_to_target_resolution() is already
    clean at this point (postprocess.py's remove_small_specks ran in
    voxel space well before meshing), so any component this small showing
    up only AFTER decimation is a decimation artifact, not segmented
    tissue that slipped through -- confirmed synthetically: a clean
    2-component watertight mesh came out of decimation with 3 extra
    3-vertex slivers and is_watertight=False; dropping those and filling
    holes restored watertightness with the real components (including a
    thin ridge) untouched. Threshold is relative to the mesh's own target
    triangle size (DECIMATION_FRAGMENT_MIN_TRIANGLES), not a fixed vertex/
    area count, so it scales with target_edge_mm instead of needing
    re-tuning per scan resolution -- the same lesson this project hit
    before with SHEET_ENHANCEMENT_SCALE_MULTIPLIERS.
    """
    components = mesh.split(only_watertight=False)
    if len(components) > 1:
        min_area = DECIMATION_FRAGMENT_MIN_TRIANGLES * triangle_area_mm2
        kept = [c for c in components if c.area >= min_area]
        dropped = [c for c in components if c.area < min_area]
        if not kept:
            kept = [max(components, key=lambda c: c.area)]
            dropped = [c for c in components if c is not kept[0]]
        # TEMPORARY (2026-08-22, remove with the other [pinna diag] prints)
        print(
            f"[pinna diag] _repair_decimation_fragments: {len(components)} components, "
            f"min_area={min_area:.4f}mm2, dropped {len(dropped)}, "
            f"dropped sizes(verts)={sorted([len(c.vertices) for c in dropped], reverse=True)}"
        )
        mesh = trimesh.util.concatenate(kept) if len(kept) > 1 else kept[0]
    trimesh.repair.fill_holes(mesh)
    return mesh


def flip_ras_lps_points(points) -> np.ndarray:
    """
    Negates X and Y of a (N, 3) point array -- converts between Slicer's
    RAS convention and plain LPS, same as io_utils.flip_ras_lps() but for
    raw point arrays rather than a sitk.Image's geometry. Self-inverse.

    Why this exists: STL (and most generic mesh formats) have no field to
    record a coordinate system, so `slicer.util.loadModel()` /
    `vtkMRMLModelStorageNode` always *assumes* a plain STL's vertex
    numbers are in LPS and flips them to RAS when loading -- see
    export_mesh()'s docstring for the full explanation.
    """
    points = np.asarray(points, dtype=float).copy()
    points[:, 0] *= -1
    points[:, 1] *= -1
    return points


def export_mesh(mesh: trimesh.Trimesh, output_path: str) -> str:
    """
    Saves the mesh to disk. Format is inferred from the file extension
    (.stl or .ply are both fine -- Curvature Project v4 reads STL for the
    scutum defect and pinna meshes, so .stl is the default expectation).

    `mesh`'s vertices are expected to be in RAS (matching every other
    physical-coordinate value in this pipeline). But STL has no field to
    record a coordinate system, and `slicer.util.loadModel()` always
    *assumes* a plain STL's raw numbers are LPS, flipping them to RAS on
    load (confirmed by the "does not contain coordinate system
    information. Using LPS." warning Slicer prints for exactly this kind
    of file). So this function writes the vertices flipped to LPS --
    matching what Slicer will assume -- so the reloaded model ends up
    positioned correctly (in true RAS) once Slicer's own load-time flip
    is applied. Any code that instead loads this file directly via
    `trimesh.load()` (bypassing Slicer, e.g. to snap Markups curve points
    onto it) must flip it back to RAS with `flip_ras_lps_points()`
    immediately after loading -- see page_scutum_draw.py /
    page_pinna_draw.py for the pattern.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    lps_mesh = trimesh.Trimesh(
        vertices=flip_ras_lps_points(mesh.vertices),
        faces=mesh.faces,
        process=False,
    )
    lps_mesh.export(output_path)
    return output_path
