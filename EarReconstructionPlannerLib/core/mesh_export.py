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

    if MESH_SMOOTHING_ITERATIONS > 0:
        trimesh.smoothing.filter_laplacian(
            mesh, iterations=MESH_SMOOTHING_ITERATIONS
        )

    return mesh


def export_mesh(mesh: trimesh.Trimesh, output_path: str) -> str:
    """
    Saves the mesh to disk. Format is inferred from the file extension
    (.stl or .ply are both fine -- Curvature Project v4 reads STL for the
    scutum defect and pinna meshes, so .stl is the default expectation).
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    mesh.export(output_path)
    return output_path
