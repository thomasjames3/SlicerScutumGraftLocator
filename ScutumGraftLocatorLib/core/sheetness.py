"""
sheetness.py
============
Multiscale Hessian-eigenvalue sheet/plate enhancement for the ear-canal
(scutum) bone-wall pipeline (core/segment_threshold.py) -- see config.py's
"Hessian-eigenvalue sheet/plate enhancement" section for the full
motivation. Two failure modes plain intensity thresholding can't fix, even
after the CurvatureFlow smoothing swap in core/smoothing.py:

  1. A genuinely thin true bone wall partial-volumes below bone_threshold
     and is missed -- the wall's local SHAPE (a thin high-intensity plate)
     is still present in the smoothed field even though its intensity
     value is gone. bright_sheetness() detects this shape directly, to
     boost recall independent of the raw HU value.
  2. The thin true air gap between the scutum and malleus still gets
     blurred above bone_threshold in the extreme 1-voxel-wide case even by
     CurvatureFlow -- the gap's local shape (a thin low-intensity septum
     between denser tissue) is likewise still detectable. dark_sheetness()
     detects this to veto a bone classification even where blurred
     intensity crept above threshold.

Adapted from the Frangi (1998)/Descoteaux et al. eigenvalue-ratio family of
vessel/plate enhancement filters. Follows core/wall_quality.py's
array/axis-order convention: sitk arrays are (z,y,x); spacing is reordered
from sitk's (x,y,z) to (z,y,x) before any scipy.ndimage call.

Pure numpy/scipy, zero Slicer dependency -- like the rest of core/, this
should be validated with synthetic analytic sheet/tube/blob/noise volumes
(see scratchpad sanity-check script) before it's trusted on real data, and
per this project's own hard-won lesson (CLAUDE.md), a synthetic pass here
validates the MATH, not the real-scan outcome -- a real-Slicer A/B is what
actually decides whether config.ENABLE_SHEET_ENHANCEMENT should be True.
"""

from __future__ import annotations
import numpy as np
from scipy import ndimage

from config import (
    SHEETNESS_ALPHA,
    SHEETNESS_BETA,
    SHEETNESS_GAMMA_AUTO_SCALE,
)

# Numerical-stability guard for Rb's denominator (see _sheet_response) --
# NOT a config.py tunable like alpha/beta/gamma, since it only exists to
# avoid a 0/0 -> nan at the exact degenerate point (an ideal flat sheet has
# both l1 and l2 -> 0), not to shape the filter's behavior. Its correct
# scale depends on the real Hessian magnitudes this module ends up seeing
# (which are in "smoothed-intensity-per-mm^2" units) -- check this value
# against real l1/l2/l3 magnitudes in the analytic sanity-check script
# before trusting it; if genuine near-degenerate sheet voxels are getting
# spuriously high Rb (and so a spuriously low sheetness response) because
# this is too small, raise it.
_EPS = 1e-6

# (dz, dy, dx) `order=` tuples for scipy.ndimage.gaussian_filter, one per
# unique entry of the symmetric 3x3 Hessian (z,y,x axis order, matching
# sitk.GetArrayFromImage()'s array layout).
_SECOND_DERIVATIVE_ORDERS = {
    ("z", "z"): (2, 0, 0),
    ("y", "y"): (0, 2, 0),
    ("x", "x"): (0, 0, 2),
    ("z", "y"): (1, 1, 0),
    ("z", "x"): (1, 0, 1),
    ("y", "x"): (0, 1, 1),
}
_AXES = ("z", "y", "x")


def _hessian_eigvals_zyx(array: np.ndarray, spacing_zyx, sigma_mm: float):
    """
    array: (z,y,x) float array -- the field to analyze (typically the same
    CurvatureFlow-smoothed field segment_threshold.py already thresholds
    against; see this module's docstring for the open question on whether
    that's actually the right choice vs. the raw cropped intensity).
    spacing_zyx: (dz, dy, dx) physical voxel size in mm. sigma_mm: the
    physical Gaussian-derivative scale to evaluate at.

    Returns (l1, l2, l3), each shaped like `array`: the 3 Hessian
    eigenvalues at every voxel, sorted so |l1| <= |l2| <= |l3| (sign
    preserved) -- the standard convention every ratio in _sheet_response
    assumes. A sheet/plate has l1~=l2~=0, l3 large; a tube has l1~=0,
    l2~=l3 large; a blob has l1~=l2~=l3, all large.

    Each second partial derivative is computed via a single
    scipy.ndimage.gaussian_filter call at a per-axis voxel sigma derived
    from sigma_mm/spacing_zyx (same mm->voxel idiom as wall_quality.py's
    per-axis radius conversion, so this stays correct on anisotropic
    spacing even though TARGET_VOXEL_SPACING_MM resampling should normally
    make this isotropic in practice), then scale-normalized by sigma_mm**2
    (Lindeberg's gamma=2 normalization for 2nd-order derivatives). Without
    this normalization, a larger sigma always yields a smaller raw
    derivative purely from additional smoothing, and multiscale_sheetness's
    per-voxel max-across-scales would degenerate into always picking the
    smallest scale regardless of the structure's true physical size.
    """
    sigma_voxels = [sigma_mm / spacing_zyx[i] for i in range(3)]

    array = array.astype(np.float64)
    second_partials = {}
    for axes, order in _SECOND_DERIVATIVE_ORDERS.items():
        second_partials[axes] = (
            ndimage.gaussian_filter(array, sigma=sigma_voxels, order=order, mode="nearest")
            * (sigma_mm ** 2)
        )

    hessian = np.empty(array.shape + (3, 3), dtype=np.float64)
    for i, axis_i in enumerate(_AXES):
        for j, axis_j in enumerate(_AXES):
            key = (axis_i, axis_j) if (axis_i, axis_j) in second_partials else (axis_j, axis_i)
            hessian[..., i, j] = second_partials[key]

    eigvals = np.linalg.eigvalsh(hessian)  # (..., 3), ascending by value
    order_by_abs = np.argsort(np.abs(eigvals), axis=-1)
    eigvals_sorted = np.take_along_axis(eigvals, order_by_abs, axis=-1)

    l1 = eigvals_sorted[..., 0]
    l2 = eigvals_sorted[..., 1]
    l3 = eigvals_sorted[..., 2]
    return l1, l2, l3


def _sheet_response(l1, l2, l3, alpha: float, beta: float, gamma: float, polarity: str) -> np.ndarray:
    """
    Frangi (1998)/Descoteaux et al.-style multiplicative eigenvalue-ratio
    sheetness measure -- see config.py's "Hessian-eigenvalue sheet/plate
    enhancement" section for the two failure modes this targets.

        Ra = |l2| / |l3|              -- ~0 for a sheet (l2~0<<l3), ~1 for a tube (l2~l3)
        Rb = |l1| / sqrt(|l2 * l3|)   -- ~0 for sheet/tube (l1~0), large for a blob
        S  = sqrt(l1^2 + l2^2 + l3^2) -- overall structure strength (noise gate)

        response = exp(-Ra^2/2a^2) * exp(-Rb^2/2b^2) * (1 - exp(-S^2/2g^2))

    All three factors are in [0,1], so the product is too.

    `polarity` selects which sign of l3 (the dominant eigenvalue) counts
    as "the structure we want", instead of re-deriving the Hessian on a
    negated field: negating the field negates every eigenvalue, which
    leaves Ra/Rb/S unchanged (all built from absolute values) and only
    flips which sign of l3 corresponds to a bright vs. dark ridge. So
    multiscale_sheetness() computes ONE Hessian per scale and calls this
    function twice against it (once per polarity) rather than computing
    the Hessian twice. 'bright' (thin high-intensity plate, e.g. true bone
    wall): a bright ridge is concave-down at its peak in the
    through-thickness direction, so l3 < 0. 'dark' (thin low-intensity
    septum, e.g. the scutum/malleus air gap): a dark valley is concave-up
    at its trough, so l3 > 0.

    Numerical note: near an ideal flat sheet, l2 -> 0 as well as l1 -> 0,
    so Rb's denominator sqrt(|l2*l3|) can itself approach 0 -- guarded
    with _EPS (module-level) rather than left to divide into nan/inf. In
    that exact limit (l1 and l2 both ~0), Rb correctly settles near 0
    (the desired sheet answer) rather than blowing up; see this module's
    _EPS comment for the case this guard does NOT fully resolve
    (near-zero l2 with non-negligible l1), which needs checking against
    the analytic sanity check, not assumed safe.
    """
    if polarity == "bright":
        polarity_mask = l3 < 0
    elif polarity == "dark":
        polarity_mask = l3 > 0
    else:
        raise ValueError(f"polarity must be 'bright' or 'dark', got {polarity!r}")

    abs_l1 = np.abs(l1)
    abs_l2 = np.abs(l2)
    abs_l3 = np.abs(l3)

    ra = abs_l2 / (abs_l3 + _EPS)
    rb = abs_l1 / (np.sqrt(abs_l2 * abs_l3) + _EPS)
    s = np.sqrt(l1 ** 2 + l2 ** 2 + l3 ** 2)

    response = (
        np.exp(-(ra ** 2) / (2 * alpha ** 2))
        * np.exp(-(rb ** 2) / (2 * beta ** 2))
        * (1.0 - np.exp(-(s ** 2) / (2 * gamma ** 2)))
    )
    return np.where(polarity_mask, response, 0.0)


def multiscale_sheetness(
    array: np.ndarray,
    spacing_zyx,
    scales_mm,
    alpha: float,
    beta: float,
    gamma_scale: float,
    polarity: str,
    calibration_mask: np.ndarray = None,
) -> np.ndarray:
    """
    Per-voxel MAX of _sheet_response across scales_mm -- a structure only
    responds strongly near its own physical scale (standard multiscale
    Frangi/Descoteaux practice), so evaluating several scales and keeping
    the best response per voxel lets both a thin gap and a thicker wall
    region get caught by the same call. Loops scale-by-scale, discarding
    each scale's Hessian before moving to the next, so peak memory is one
    scale's Hessian plus the running best-response array, not all scales
    held at once.

    `gamma_scale` replaces this module's original fixed, hand-picked
    `gamma` (a single absolute number, e.g. "100.0") -- see config.py's
    SHEETNESS_GAMMA_AUTO_SCALE for why. At EACH scale, the actual noise-
    gate value fed to _sheet_response is computed fresh as
    `gamma_scale * median(S)`, where S (the structure-strength term) is
    computed over `array` at THIS scale -- i.e. self-calibrated per scan
    and per scale, adapted from the published Krcah et al. bone-sheetness
    filter (which computes its equivalent constant from the image's own
    Hessian-trace statistics rather than a fixed value -- see
    https://github.com/ypauchard/ITK-KrcahSheetnessImageFilter). Three
    rounds of real-scan feedback on the old fixed-gamma design each found
    it needed re-guessing whenever scan spacing or HU scale changed, with
    no warning it had silently stopped working -- this makes that
    adaptation automatic instead of requiring a new hand-picked constant
    every time.

    `calibration_mask`, if given, restricts the median(S) statistic to
    those voxels (e.g. segment_threshold.py passes its `shell_array`) --
    the response itself is still computed and returned for every voxel in
    `array`, only the calibration statistic is masked. Matters a lot in
    practice: a first version of this used mean(S) over the WHOLE
    cropped_image and found it barely gated anything (the true noise-vs-
    signal distinction got washed out by however much of the crop was
    plain empty background, dragging the statistic down to near
    meaningless), while median(S) restricted to the shell -- the region
    the final decision actually gets made in -- gave a self-calibrated
    value that worked well. Defaults to None (whole `array`) for
    standalone/test convenience, but segment_threshold.py always passes
    its shell_array explicitly.
    """
    best = None
    for sigma_mm in scales_mm:
        l1, l2, l3 = _hessian_eigvals_zyx(array, spacing_zyx, sigma_mm)
        s = np.sqrt(l1 ** 2 + l2 ** 2 + l3 ** 2)
        s_for_calibration = s[calibration_mask] if calibration_mask is not None else s
        gamma = gamma_scale * np.median(s_for_calibration)
        response = _sheet_response(l1, l2, l3, alpha, beta, gamma, polarity)
        best = response if best is None else np.maximum(best, response)
    return best


def bright_sheetness(
    array: np.ndarray,
    spacing_zyx,
    scales_mm,
    alpha: float = SHEETNESS_ALPHA,
    beta: float = SHEETNESS_BETA,
    gamma_scale: float = SHEETNESS_GAMMA_AUTO_SCALE,
    calibration_mask: np.ndarray = None,
) -> np.ndarray:
    """Boosts recall for thin bright wall structure below bone_threshold --
    see config.BRIGHT_SHEETNESS_THRESHOLD. `scales_mm` has no config-based
    default -- unlike alpha/beta/gamma_scale, the right scales depend on
    the scan's own native voxel spacing (see config.py's
    SHEET_ENHANCEMENT_SCALE_MULTIPLIERS), so the caller (segment_threshold.
    segment_bone_wall()) always computes and passes them explicitly.
    `calibration_mask` -- see multiscale_sheetness()'s docstring.
    Returns the raw [0,1] response (not pre-thresholded), so it stays
    inspectable in tests without re-deriving it."""
    return multiscale_sheetness(
        array, spacing_zyx, scales_mm, alpha, beta, gamma_scale, "bright", calibration_mask
    )


def dark_sheetness(
    array: np.ndarray,
    spacing_zyx,
    scales_mm,
    alpha: float = SHEETNESS_ALPHA,
    beta: float = SHEETNESS_BETA,
    gamma_scale: float = SHEETNESS_GAMMA_AUTO_SCALE,
    calibration_mask: np.ndarray = None,
) -> np.ndarray:
    """Vetoes bone classification for a thin dark septum (e.g. the
    scutum/malleus air gap) -- see config.DARK_SHEETNESS_THRESHOLD.
    `scales_mm` has no config-based default -- see bright_sheetness()'s
    docstring. `calibration_mask` -- see multiscale_sheetness()'s
    docstring. Returns the raw [0,1] response (not pre-thresholded)."""
    return multiscale_sheetness(
        array, spacing_zyx, scales_mm, alpha, beta, gamma_scale, "dark", calibration_mask
    )
