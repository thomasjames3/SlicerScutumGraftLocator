"""
signature.py
------------
A "signature" bundles everything we use to compare two surface patches:
  - a histogram of shape index values (what *type* of curvature is
    present) -- extrinsic, changes if the surface is bent
  - a histogram of curvedness values (how *strongly* curved) -- also
    extrinsic
  - a histogram of geodesic distance-from-center values -- intrinsic,
    stays the same under bending (only changes if the surface stretches)
  - simple boundary/footprint descriptors (area, perimeter, compactness,
    aspect ratio)

Comparing signatures (see scoring.py) never needs the two patches to be
aligned in 3D space -- everything here is pose-invariant. The curvature
histograms are *not* bending-invariant (a flat sheet and the same sheet
bent into a dome have different curvature everywhere, despite being the
same physical patch of material) -- the radial-distance histogram is
included specifically to compensate for that, since cartilage bends much
more readily than it stretches, so two patches with different curvature
but a similar intrinsic (geodesic) shape may still be a good match.

Ported verbatim from the standalone Curvature Project v4 -- this file
never touched any of the three dependencies that forced the old
subprocess architecture.
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import trimesh

from .descriptors import Descriptors
from .geodesics import BoundaryShape, boundary_shape_descriptors


@dataclass
class ShapeSignature:
    shape_index_hist: np.ndarray
    curvedness_hist: np.ndarray
    radial_distance_hist: np.ndarray
    boundary: BoundaryShape
    n_vertices: int


def compute_signature(
    mesh: trimesh.Trimesh,
    descriptors: Descriptors,
    hist_bins: int,
    radial_distances: np.ndarray,
    radial_distance_range: tuple[float, float],
    shape_index_range: tuple[float, float] = (-1.0, 1.0),
    curvedness_range: tuple[float, float] = (0.0, 1.0),
) -> ShapeSignature:
    """
    Build a ShapeSignature for a mesh/patch given its per-vertex descriptors
    and its geodesic distance-from-center values.

    `curvedness_range` should be chosen based on the *pinna* mesh as a
    whole (e.g. a high percentile of its curvedness values) so that both
    the defect and every candidate patch are histogrammed on the same
    scale -- otherwise histogram comparisons aren't meaningful.

    `radial_distance_range` should typically be (0, patch_radius), the
    same radius used to size every candidate patch, so the defect and
    every candidate are compared on the same intrinsic scale.
    """
    si_hist, _ = np.histogram(
        descriptors.shape_index, bins=hist_bins, range=shape_index_range,
        density=True,
    )
    curv_hist, _ = np.histogram(
        descriptors.curvedness, bins=hist_bins, range=curvedness_range,
        density=True,
    )
    radial_hist, _ = np.histogram(
        radial_distances, bins=hist_bins, range=radial_distance_range,
        density=True,
    )

    boundary = boundary_shape_descriptors(mesh)

    return ShapeSignature(
        shape_index_hist=si_hist,
        curvedness_hist=curv_hist,
        radial_distance_hist=radial_hist,
        boundary=boundary,
        n_vertices=len(mesh.vertices),
    )
