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
from skimage import measure

from config import MESH_SMOOTHING_ITERATIONS


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
