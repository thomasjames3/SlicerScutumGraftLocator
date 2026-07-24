"""
candidates.py
-------------
Generates candidate harvest-site centers spread evenly across the pinna
surface, so we screen the whole ear rather than an arbitrary/biased subset.

Ported verbatim from the standalone Curvature Project v4 -- this file
never touched any of the three dependencies that forced the old
subprocess architecture.
"""

from __future__ import annotations
import numpy as np
import trimesh
from scipy.spatial import cKDTree


def generate_candidate_vertices(mesh: trimesh.Trimesh, num_candidates: int,
                                 seed: int = 0) -> np.ndarray:
    """
    Pick `num_candidates` vertex indices on `mesh`, spread roughly evenly
    across the surface (Poisson-disk-like via trimesh's even sampling),
    by sampling points on the surface and snapping each to its nearest
    actual mesh vertex (so we can reuse vertex-based geodesic distances
    computed elsewhere).
    """
    np.random.seed(seed)
    points, _ = trimesh.sample.sample_surface_even(mesh, num_candidates)

    if len(points) < num_candidates:
        # sample_surface_even can return fewer points than requested for
        # small/awkward meshes -- top up with plain random surface samples.
        extra_needed = num_candidates - len(points)
        extra_points, _ = trimesh.sample.sample_surface(mesh, extra_needed)
        points = np.vstack([points, extra_points])

    tree = cKDTree(mesh.vertices)
    _, vertex_ids = tree.query(points)
    vertex_ids = np.unique(vertex_ids)  # drop accidental duplicates

    return vertex_ids
