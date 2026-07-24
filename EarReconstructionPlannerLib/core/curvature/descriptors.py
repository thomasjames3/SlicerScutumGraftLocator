"""
descriptors.py
--------------
Per-vertex intrinsic shape descriptors: shape index and curvedness
(Koenderink & van Doorn, 1992).

The standalone Curvature Project v4 computed these via PyMeshLab's curvature
filter. PyMeshLab is a compiled extension with Windows wheels built for
specific Python versions, which is exactly the kind of dependency that
can't be trusted to install into Slicer's own bundled Python -- that's what
originally forced this whole comparison to run as a separate subprocess in
its own venv. Ported here instead to run on trimesh's own discrete
curvature measures (trimesh is already a required, working dependency of
this extension's segmentation pipeline -- see ../../dependencies.py), so
this now runs entirely in-process with no extra install.

trimesh.curvature.discrete_gaussian_curvature_measure() /
discrete_mean_curvature_measure() (Cohen-Steiner & Morvan's normal-cycle
method) return curvature *integrated over a ball* of the given radius
around each point, not the raw per-point Gaussian (K) / mean (H)
curvature -- dividing by the ball's flat-disc area (pi * radius^2) gives a
standard first-order estimate of K and H themselves. This approximation
was checked against a synthetic sphere mesh (which has a known, exact
analytic curvature: K = 1/r^2, H = 1/r) before trusting it on real
anatomy -- see the project's test suite.

Note: trimesh's own discrete_mean_curvature_measure() internally uses
mesh.face_adjacency_tree, an R-tree spatial index that requires the
optional `rtree` package -- which isn't installed by this extension's
Setup page and would have been a genuinely new dependency (unlike
everything else in this port). Rather than add it, `_mean_curvature_
measure()` below reimplements the same normal-cycle sum using
mesh.face_adjacency_edges/angles/convex (all already pure numpy, no
rtree) plus a scipy cKDTree over edge midpoints to find candidate edges
near each query point (a KDTree radius search is a safe, if slightly
looser, substitute for the R-tree's bounding-box search here -- any edge
that could intersect the ball is still found, just possibly a few extra
false candidates that trimesh's own line_ball_intersection() correctly
scores as zero-length). This keeps the whole port to a strict zero-new-
dependency change.

Shape index (S) describes the *type* of local curvature on a continuous
scale from -1 (cup) to +1 (dome), passing through saddle (0), rut (-0.5)
and ridge (+0.5). Curvedness (C) describes the *magnitude* of curvature,
independent of type. A flat plate and a gently-curved plate can have the
same shape index but very different curvedness.
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import trimesh
from trimesh import curvature as tm_curvature
from scipy.spatial import cKDTree


@dataclass
class Descriptors:
    shape_index: np.ndarray   # roughly in [-1, 1], type of curvature
    curvedness: np.ndarray    # >= 0, magnitude of curvature
    reliable: np.ndarray      # bool; False where the curvature ball extended
                               # past an open mesh boundary (see
                               # boundary_unreliable_mask) -- shape_index/
                               # curvedness at these vertices are numerical
                               # artifacts of the missing surface, not real
                               # shape, and should be excluded from any
                               # histogram/matching that assumes the value
                               # reflects true local geometry.
    curvature_radius: float   # the ball radius these were estimated with --
                               # exposed so callers building per-patch
                               # Descriptors from a slice of a larger mesh's
                               # arrays (see pipeline.py) can compute a
                               # patch-local `reliable` mask with the same
                               # radius the underlying values were estimated
                               # with.


def boundary_unreliable_mask(mesh: trimesh.Trimesh, radius: float) -> np.ndarray:
    """
    True for every vertex within `radius` of an open mesh boundary edge.

    The ball-based curvature measures used in compute_descriptors() assume
    the ball around each point is filled with real mesh surface; near an
    open boundary (any edge used by only one face) part of that ball
    extends past where the mesh actually ends. There's no real geometry
    missing there -- the patch was just cut short -- but the angle-deficit
    math can't tell the difference, so it reads the missing surface as a
    sharp, large curvature. This was confirmed directly against a real
    isolated defect patch: ~80% of its vertices had a numerically invalid
    (negative) H^2-K discriminant with Gaussian curvature estimates several
    times larger than anywhere else on the mesh, all within one ball radius
    of the patch's own drawn-outline boundary -- exactly this effect, not
    true anatomy. compute_signature() uses this mask to exclude those
    vertices from shape_index/curvedness histograms so patch-vs-patch
    comparisons aren't dominated by cut-edge noise. Every patch used in
    this comparison (the defect, and every candidate cut from the pinna
    with GeodesicPatchExtractor) has this same kind of boundary, so this
    matters for all of them, not just unusually small/rough meshes.
    """
    from trimesh.grouping import group_rows

    boundary_edge_rows = group_rows(mesh.edges_sorted, require_count=1)
    if len(boundary_edge_rows) == 0:
        return np.zeros(len(mesh.vertices), dtype=bool)

    boundary_edges = mesh.edges_sorted[boundary_edge_rows]
    boundary_vertex_ids = np.unique(boundary_edges)
    boundary_points = mesh.vertices[boundary_vertex_ids]

    tree = cKDTree(boundary_points)
    dist, _ = tree.query(mesh.vertices)
    return dist <= radius


def _mean_curvature_measure(mesh: trimesh.Trimesh, points: np.ndarray,
                             radius: float) -> np.ndarray:
    """
    Same computation as trimesh.curvature.discrete_mean_curvature_measure()
    (sum of angle * edge-length-within-ball * convexity-sign, over every
    face-adjacency edge within `radius` of each point), but finds candidate
    edges via a scipy cKDTree over edge midpoints instead of trimesh's own
    R-tree-based face_adjacency_tree -- see this module's docstring for why.
    """
    edges = mesh.face_adjacency_edges
    if len(edges) == 0:
        return np.zeros(len(points))

    edge_starts = mesh.vertices[edges[:, 0]]
    edge_ends = mesh.vertices[edges[:, 1]]
    edge_midpoints = 0.5 * (edge_starts + edge_ends)
    edge_half_lengths = 0.5 * np.linalg.norm(edge_ends - edge_starts, axis=1)

    # Any edge that could intersect the ball around a point must have its
    # midpoint within (radius + that edge's own half-length) of the point.
    # Using the max half-length across all edges as a single query radius
    # is a safe (if slightly loose) upper bound -- false-candidate edges
    # this pulls in are simply scored as zero-length by
    # line_ball_intersection() below.
    max_half_length = float(edge_half_lengths.max())
    tree = cKDTree(edge_midpoints)
    candidate_lists = tree.query_ball_point(points, radius + max_half_length)

    angles = mesh.face_adjacency_angles
    signs = np.where(mesh.face_adjacency_convex, 1.0, -1.0)

    mean_curv = np.zeros(len(points))
    for i, (point, candidates) in enumerate(zip(points, candidate_lists)):
        if not candidates:
            continue
        candidates = np.asarray(candidates, dtype=int)
        lengths = tm_curvature.line_ball_intersection(
            edge_starts[candidates], edge_ends[candidates],
            center=point, radius=radius,
        )
        mean_curv[i] = (lengths * angles[candidates] * signs[candidates]).sum() / 2.0

    return mean_curv


def compute_descriptors(mesh: trimesh.Trimesh,
                         radius_edge_multiplier: float = 3.0) -> Descriptors:
    """
    Compute per-vertex shape index and curvedness from principal
    curvatures estimated via trimesh's discrete curvature measures.

    `radius_edge_multiplier` sets the curvature-estimation ball radius as
    a multiple of this specific mesh's own mean edge length, rather than a
    fixed mm value, so the estimate adapts automatically to meshes of
    different density/resolution (see config.py's
    CURVATURE_MEASURE_RADIUS_EDGE_MULTIPLIER).
    """
    points = mesh.vertices.view(np.ndarray)

    mean_edge_length = float(mesh.edges_unique_length.mean())
    radius = mean_edge_length * radius_edge_multiplier
    if not np.isfinite(radius) or radius <= 0:
        raise ValueError(
            "Could not determine a curvature estimation radius for this "
            "mesh -- it may be degenerate (no valid edges)."
        )

    gaussian_measure = tm_curvature.discrete_gaussian_curvature_measure(
        mesh, points, radius
    )
    mean_measure = _mean_curvature_measure(mesh, points, radius)

    # First-order normalization: divide the ball-integrated measure by the
    # flat-disc area of that ball to recover an estimate of the curvature
    # itself. Good enough here because these values only ever feed into a
    # relative (histogram-based) comparison between the defect and
    # candidates, computed with this exact same normalization every time --
    # not used as calibrated, absolute curvature values anywhere else.
    ball_area = np.pi * radius ** 2
    gaussian_curvature = gaussian_measure / ball_area  # K
    mean_curvature = mean_measure / ball_area           # H

    # Principal curvatures from H and K: k1,k2 = H +/- sqrt(H^2 - K).
    # Clip the discriminant at 0 to absorb small numerical noise that would
    # otherwise occasionally make it slightly negative at near-umbilic
    # points.
    discriminant = np.clip(mean_curvature ** 2 - gaussian_curvature, 0.0, None)
    sqrt_disc = np.sqrt(discriminant)
    k1 = mean_curvature + sqrt_disc  # k1 >= k2 by construction
    k2 = mean_curvature - sqrt_disc

    curvedness = np.sqrt((k1 ** 2 + k2 ** 2) / 2.0)

    # Koenderink & van Doorn's shape index: S = (2/pi) * atan((k1+k2)/(k1-k2)).
    # Using atan2 instead of a plain ratio + atan means umbilic points
    # (k1 == k2, where the ratio would divide by zero) resolve to the
    # correct limiting value automatically: a fully convex spherical point
    # (k1 == k2 > 0) correctly gives S -> +1, a fully concave one gives
    # S -> -1, and a genuinely flat point (k1 == k2 == 0) gives S = 0.
    shape_index = (2.0 / np.pi) * np.arctan2(k1 + k2, k1 - k2)

    shape_index = np.nan_to_num(shape_index, nan=0.0)
    curvedness = np.nan_to_num(curvedness, nan=0.0)

    n_verts = len(mesh.vertices)
    if shape_index.shape[0] != n_verts or curvedness.shape[0] != n_verts:
        raise RuntimeError(
            "Curvature computation returned a different vertex count than "
            "the input mesh -- this shouldn't happen."
        )

    reliable = ~boundary_unreliable_mask(mesh, radius)

    return Descriptors(shape_index=shape_index, curvedness=curvedness,
                        reliable=reliable, curvature_radius=radius)
