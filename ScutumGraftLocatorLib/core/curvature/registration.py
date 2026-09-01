"""
registration.py
----------------
Fine-grained refinement stage, only run on the top-N% of candidates that
already passed the coarse (registration-free) screening in scoring.py.

For each shortlisted candidate patch, we:
  1. Center both the defect and the candidate patch point clouds.
  2. Try several initial rotations based on PCA axes of each point cloud
     (PCA gives you the right *axes*, but not their sign/handedness, so
     we try multiple sign combinations rather than guessing one).
  3. Run ICP from each initial guess and keep whichever converges to the
     lowest cost.
  4. Report symmetric Chamfer and Hausdorff distance after alignment as
     the refined similarity metrics.

This is deliberately simpler than a full FPFH + RANSAC global registration
pipeline: by this stage the coarse filter has already found patches that
are plausibly similar in shape, so we only need to search a small space
of initial poses (PCA axis sign flips), not a fully unconstrained global
registration. This is both faster and more robust on small, potentially
noisy patches than feature-based registration.

The standalone Curvature Project v4 used open3d's point-to-plane ICP here.
open3d is a large compiled extension -- the same category of dependency
(alongside pymeshlab and potpourri3d) that originally forced this whole
comparison to run as a subprocess in a separate venv rather than directly
in Slicer's own Python. Ported here to use trimesh.registration.icp
instead (trimesh is already a required dependency of this extension's
segmentation pipeline -- see ../../dependencies.py), with
reflection=False, scale=False so it stays a pure rigid-body alignment,
matching the original's intent. This is point-to-point ICP (nearest-
neighbor correspondences) rather than point-to-plane (which additionally
uses surface normals) -- point-to-plane typically converges a bit faster/
more robustly on smooth surfaces, but point-to-point is a completely
standard, well-understood alternative, and this stage's own docstring
above already frames it as deliberately simple rather than a from-scratch
global registration pipeline.
"""

from __future__ import annotations
from dataclasses import dataclass
from itertools import product

import numpy as np
import trimesh
from trimesh import registration as tm_registration
from scipy.spatial import cKDTree


@dataclass
class RefinementResult:
    transform: np.ndarray          # 4x4, maps candidate patch -> defect frame
    fitness: float                 # fraction of points within the inlier threshold after alignment (higher = more overlap)
    inlier_rmse: float             # RMS distance among inlier correspondences (lower = better)
    chamfer_distance: float        # symmetric mean nearest-neighbor distance
    hausdorff_distance: float      # symmetric max nearest-neighbor distance


def _pca_axes(points: np.ndarray) -> np.ndarray:
    """Return a 3x3 matrix whose columns are the PCA axes, largest first."""
    centered = points - points.mean(axis=0)
    cov = np.cov(centered.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]
    axes = eigvecs[:, order]
    return axes


def _candidate_initial_rotations(source_points: np.ndarray,
                                  target_points: np.ndarray) -> list:
    """
    Build a handful of plausible initial rotation matrices that align the
    PCA axes of `source_points` onto the PCA axes of `target_points`,
    trying all sign combinations for the first two axes (the third axis
    is fixed by the cross product, to keep a right-handed, non-mirrored
    rotation -- we don't want to accidentally flip the patch inside out).
    """
    src_axes = _pca_axes(source_points)
    tgt_axes = _pca_axes(target_points)

    rotations = []
    for sx, sy in product([1, -1], repeat=2):
        src = src_axes.copy()
        src[:, 0] *= sx
        src[:, 1] *= sy
        src[:, 2] = np.cross(src[:, 0], src[:, 1])  # enforce right-handedness

        # R maps src_axes -> tgt_axes: R @ src = tgt  =>  R = tgt @ src^T
        r = tgt_axes @ src.T
        rotations.append(r)
    return rotations


def refine_candidate(candidate_patch: trimesh.Trimesh,
                      defect_mesh: trimesh.Trimesh,
                      icp_max_iterations: int = 60,
                      icp_cost_threshold: float = 1e-9,
                      inlier_threshold_scale: float = 0.5) -> RefinementResult:
    """
    Align `candidate_patch` onto `defect_mesh` and report post-alignment
    similarity metrics. `inlier_threshold_scale` is multiplied by the
    defect's bounding-sphere radius to get a correspondence distance
    threshold (used only for the `fitness`/`inlier_rmse` diagnostics
    below) that scales sensibly with the size of your models.
    """
    src_points = candidate_patch.vertices
    tgt_points = defect_mesh.vertices

    src_centroid = src_points.mean(axis=0)
    tgt_centroid = tgt_points.mean(axis=0)
    src_centered = src_points - src_centroid
    tgt_centered = tgt_points - tgt_centroid

    defect_radius = float(np.linalg.norm(tgt_centered, axis=1).max())
    inlier_threshold = max(defect_radius * inlier_threshold_scale, 1e-6)

    rotations = _candidate_initial_rotations(src_centered, tgt_centered)

    best_matrix = None
    best_cost = np.inf

    for r in rotations:
        init_transform = np.eye(4)
        init_transform[:3, :3] = r

        try:
            matrix, _transformed, cost = tm_registration.icp(
                src_centered, tgt_centered,
                initial=init_transform,
                threshold=icp_cost_threshold,
                max_iterations=icp_max_iterations,
                reflection=False,
                scale=False,
            )
        except (np.linalg.LinAlgError, ValueError):
            # Degenerate point configuration for this particular initial
            # rotation (e.g. a near-coincident/collinear patch) -- skip it
            # and try the remaining candidate rotations.
            continue

        if cost < best_cost:
            best_cost = cost
            best_matrix = matrix

    if best_matrix is None:
        # Every initial rotation failed -- return a clearly "bad" result
        # rather than crashing the whole pipeline.
        return RefinementResult(
            transform=np.eye(4), fitness=0.0, inlier_rmse=np.inf,
            chamfer_distance=np.inf, hausdorff_distance=np.inf,
        )

    # Apply best transform to the (centered) candidate points, then
    # measure symmetric Chamfer / Hausdorff distance against the
    # (centered) defect points.
    aligned = (best_matrix[:3, :3] @ src_centered.T).T + best_matrix[:3, 3]

    tree_defect = cKDTree(tgt_centered)
    tree_candidate = cKDTree(aligned)

    d_cand_to_defect, _ = tree_defect.query(aligned)
    d_defect_to_cand, _ = tree_candidate.query(tgt_centered)

    chamfer = float(0.5 * (d_cand_to_defect.mean() + d_defect_to_cand.mean()))
    hausdorff = float(max(d_cand_to_defect.max(), d_defect_to_cand.max()))

    inlier_mask = d_cand_to_defect <= inlier_threshold
    fitness = float(inlier_mask.mean()) if len(inlier_mask) else 0.0
    inlier_rmse = (
        float(np.sqrt(np.mean(d_cand_to_defect[inlier_mask] ** 2)))
        if inlier_mask.any() else np.inf
    )

    # Compose the full transform, including the centroid shifts we
    # applied before running ICP, so `transform` maps the *original*
    # candidate patch vertices into the *original* defect mesh frame.
    to_src_centered = np.eye(4)
    to_src_centered[:3, 3] = -src_centroid

    from_tgt_centered = np.eye(4)
    from_tgt_centered[:3, 3] = tgt_centroid

    full_transform = from_tgt_centered @ best_matrix @ to_src_centered

    return RefinementResult(
        transform=full_transform,
        fitness=fitness,
        inlier_rmse=inlier_rmse,
        chamfer_distance=chamfer,
        hausdorff_distance=hausdorff,
    )
