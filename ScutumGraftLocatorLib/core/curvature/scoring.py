"""
scoring.py
----------
Turns two ShapeSignatures into a single "coarse score" -- lower is a
better match. This never touches 3D position/orientation, so it's cheap
enough to run on every candidate on the whole pinna.

Ported verbatim from the standalone Curvature Project v4 -- this file
never touched any of the three dependencies that forced the old
subprocess architecture.
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np

from .signature import ShapeSignature


@dataclass
class ScoreWeights:
    shape_index: float = 1.0
    curvedness: float = 1.0
    radial_distance: float = 1.5   # intrinsic/bending-invariant term; see
                                     # signature.py's docstring for why this
                                     # is weighted at least as heavily as the
                                     # (bending-*variant*) curvature terms
    compactness: float = 0.5
    aspect_ratio: float = 0.5
    area: float = 0.25


def chi_square_distance(h1: np.ndarray, h2: np.ndarray, eps: float = 1e-10) -> float:
    """Symmetric chi-square distance between two histograms."""
    num = (h1 - h2) ** 2
    den = h1 + h2 + eps
    return float(np.sum(num / den))


def coarse_score(defect: ShapeSignature, candidate: ShapeSignature,
                  weights: ScoreWeights = ScoreWeights()) -> float:
    """
    Weighted combination of shape-index histogram distance, curvedness
    histogram distance, radial-distance (intrinsic/geodesic) histogram
    distance, and simple boundary-shape differences.

    The shape-index and curvedness terms are *extrinsic* -- they change
    if the surface is bent, even without any stretching. The
    radial-distance term is *intrinsic* -- it only changes if the
    surface stretches. Since cartilage bends far more readily than it
    stretches, a candidate with different curvature but a similar
    intrinsic (geodesic) shape may still be a good match -- the
    radial-distance term is what lets the coarse score reflect that,
    rather than penalizing every curvature mismatch equally regardless
    of whether it's the kind of mismatch bending could plausibly fix.

    Lower score = more similar to the defect.
    """
    si_dist = chi_square_distance(defect.shape_index_hist, candidate.shape_index_hist)
    curv_dist = chi_square_distance(defect.curvedness_hist, candidate.curvedness_hist)
    radial_dist = chi_square_distance(defect.radial_distance_hist,
                                       candidate.radial_distance_hist)

    d_boundary = defect.boundary
    c_boundary = candidate.boundary

    compactness_diff = abs(d_boundary.compactness - c_boundary.compactness)
    aspect_diff = abs(d_boundary.aspect_ratio - c_boundary.aspect_ratio)

    # Compare area on a log scale so a candidate 2x too big is penalized
    # the same as one 2x too small.
    area_ratio = (c_boundary.area + 1e-9) / (d_boundary.area + 1e-9)
    area_diff = abs(np.log(area_ratio))

    score = (
        weights.shape_index * si_dist
        + weights.curvedness * curv_dist
        + weights.radial_distance * radial_dist
        + weights.compactness * compactness_diff
        + weights.aspect_ratio * aspect_diff
        + weights.area * area_diff
    )
    return float(score)
