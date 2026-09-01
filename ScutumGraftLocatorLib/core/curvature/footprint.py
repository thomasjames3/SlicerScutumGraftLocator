"""
footprint.py
------------
The geodesic-disc "patches" used for coarse screening and ICP alignment
are deliberately simple (a disc sized to roughly match the defect's
diameter) -- good enough for shape comparison, but not a realistic
picture of what to actually cut from the pinna.

Once a candidate has been through ICP refinement, though, we already
have a rigid transform that aligns that candidate's patch onto the
defect. This module uses the *inverse* of that transform to project the
defect's own true shape -- its full footprint and its boundary outline
-- back onto the pinna surface at that site. The result is a highlighted
region shaped like the actual defect, not a circle.

Ported verbatim from the standalone Curvature Project v4 -- this file
never touched any of the three dependencies that forced the old
subprocess architecture.
"""

from __future__ import annotations
import numpy as np
import trimesh
from scipy.spatial import cKDTree


def apply_rigid_transform(transform: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Apply a 4x4 homogeneous rigid transform to an (N, 3) array of points."""
    homogeneous = np.hstack([points, np.ones((points.shape[0], 1))])
    transformed = (transform @ homogeneous.T).T
    return transformed[:, :3]


def project_defect_footprint(
    defect_mesh: trimesh.Trimesh,
    defect_boundary_local_ids: np.ndarray,
    candidate_to_defect_transform: np.ndarray,
    target_tree: cKDTree,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Map the defect's shape onto the surface represented by `target_tree`
    (built from the pinna's vertices) at one specific harvest site, using
    the inverse of the rigid transform ICP found when aligning that
    site's patch onto the defect.

    Parameters
    ----------
    defect_mesh : trimesh.Trimesh
        The scutum defect mesh (whole mesh, as used elsewhere in the
        pipeline).
    defect_boundary_local_ids : np.ndarray
        Indices into `defect_mesh.vertices` giving its boundary loop
        (see heatmap.boundary_vertex_local_indices).
    candidate_to_defect_transform : np.ndarray
        The 4x4 transform returned by registration.refine_candidate(),
        which maps a candidate patch's original vertices into the
        defect's frame. We invert it here to go the other way.
    target_tree : scipy.spatial.cKDTree
        A KD-tree built from the pinna's vertices, used to snap the
        projected defect points onto actual pinna vertices.

    Returns
    -------
    fill_vertex_ids : np.ndarray
        Pinna vertex indices nearest to every (transformed) defect
        vertex -- an approximation of the defect's full footprint at
        this site.
    outline_vertex_ids : np.ndarray
        Pinna vertex indices nearest to the (transformed) defect
        boundary only -- the footprint's outline.
    """
    inverse_transform = np.linalg.inv(candidate_to_defect_transform)

    projected_all = apply_rigid_transform(inverse_transform, defect_mesh.vertices)
    _, fill_vertex_ids = target_tree.query(projected_all)

    projected_boundary = apply_rigid_transform(
        inverse_transform, defect_mesh.vertices[defect_boundary_local_ids]
    )
    _, outline_vertex_ids = target_tree.query(projected_boundary)

    return np.unique(fill_vertex_ids), np.unique(outline_vertex_ids)
