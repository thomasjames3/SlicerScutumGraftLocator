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
    GAUSSIAN_SMOOTHING_SIGMA_MM,
    SUBVOXEL_MESH_BAND_MM,
    SUBVOXEL_MESH_SAFETY_MARGIN_HU,
)


class EmptySegmentationError(ValueError):
    """Raised when a label map has no (or no meaningful) foreground to mesh.

    marching_cubes requires level=0.5 to fall strictly within the volume's
    min/max, which only fails when the label map is uniformly 0 or 1 --
    i.e. segmentation found nothing (or everything). Callers should catch
    this and prompt the surgeon to adjust thresholds/landmarks rather than
    letting skimage's cryptic ValueError surface.
    """


def label_map_to_mesh(label_image: sitk.Image) -> trimesh.Trimesh:
    """
    Runs marching cubes on a binary label map to produce a surface mesh in
    physical (mm) coordinates, then applies light Laplacian smoothing.

    Parameters
    ----------
    label_image : sitk.Image
        The final, surgeon-approved segmentation (UInt8, 1 = canal).

    Returns
    -------
    trimesh.Trimesh
        A watertight-ish surface mesh in the scan's physical coordinate
        space (same space Slicer displays everything in), so it lines up
        correctly if loaded alongside the original scan or other meshes
        from the same patient.
    """
    array = sitk.GetArrayFromImage(label_image)  # (z, y, x) order
    spacing = label_image.GetSpacing()  # (x, y, z) order

    if array.min() == array.max():
        # Uniform label map (almost always all-zero in practice) -- no
        # surface for marching_cubes to find. Raise a clear, catchable
        # error instead of letting skimage's "Surface level must be within
        # volume data range" ValueError surface to the surgeon.
        raise EmptySegmentationError(
            "The segmentation is empty -- no bone wall was found. Try "
            "adjusting the air/bone threshold sliders, or go back and "
            "double-check the landmark placement."
        )

    # marching_cubes expects the spacing tuple in the same axis order as
    # the array it's given, i.e. (z, y, x).
    array_spacing = (spacing[2], spacing[1], spacing[0])

    verts, faces, normals, _ = measure.marching_cubes(
        array, level=0.5, spacing=array_spacing
    )

    return _verts_faces_to_trimesh(verts, faces, label_image)


def label_map_to_mesh_subvoxel(
    cropped_image: sitk.Image,
    label_image: sitk.Image,
    raw_threshold_mask: sitk.Image,
    threshold_value: float,
    smoothing_sigma_mm: float = GAUSSIAN_SMOOTHING_SIGMA_MM,
    band_radius_mm: float = SUBVOXEL_MESH_BAND_MM,
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
    smoothing_sigma_mm : float
        Must match segment_bone_wall()'s own smoothing sigma (defaults to
        the same config constant) so this samples the identical field.
    band_radius_mm : float
        How far from the mask boundary (on both sides) to trust the real
        smoothed intensity before blending toward the safety clamp. See
        config.SUBVOXEL_MESH_BAND_MM for tuning guidance.
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

    smoothed = sitk.SmoothingRecursiveGaussian(cropped_image, sigma=smoothing_sigma_mm)
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
    clamp_target = np.where(
        signed_dist_mm > 0,
        threshold_value + SUBVOXEL_MESH_SAFETY_MARGIN_HU,
        threshold_value - SUBVOXEL_MESH_SAFETY_MARGIN_HU,
    )
    blended_array = (1.0 - weight) * smoothed_array + weight * clamp_target

    verts, faces, normals, _ = measure.marching_cubes(
        blended_array, level=threshold_value, spacing=sampling_zyx
    )

    return _verts_faces_to_trimesh(verts, faces, label_image)


def _verts_faces_to_trimesh(verts: np.ndarray, faces: np.ndarray, label_image: sitk.Image) -> trimesh.Trimesh:
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

    if MESH_SMOOTHING_ITERATIONS > 0:
        trimesh.smoothing.filter_laplacian(
            mesh, iterations=MESH_SMOOTHING_ITERATIONS
        )

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
