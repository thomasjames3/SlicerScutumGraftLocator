"""
smoothing.py
============
Shared pre-thresholding denoise step for the ear-canal (scutum) pipeline --
core/segment_threshold.py's air-lumen and bone-wall thresholding, core/
threshold_seeds.py's calibration sampling, and core/mesh_export.py's
sub-voxel mesh extraction all need to threshold against the EXACT same
smoothed field, or their results silently disagree. Before this module
existed, that was enforced only by each call site independently
replicating a "must match segment_bone_wall's own smoothing" comment --
this makes it a structural guarantee (one function, one place to change)
instead of a documentation-based convention four different files had to
stay in sync with by hand.

Not used by the pinna pipeline (core/segment_pinna_threshold.py), which
keeps its own plain Gaussian smoothing (config.GAUSSIAN_SMOOTHING_SIGMA_MM)
-- that pipeline is already confirmed working end-to-end and wasn't
exhibiting the problem described below, so it's deliberately left alone.

Why CurvatureFlow instead of a plain Gaussian blur: a real, reported
failure was the scutum and the malleus reading as fused in the exported
mesh, because the thin true air gap between them gets blurred above
bone_threshold by an indiscriminate Gaussian blur before thresholding ever
runs -- no threshold value can fix that after the fact, since the blurred
data genuinely no longer contains the distinction. CurvatureFlow (edge-
preserving diffusion -- it smooths within regions but doesn't diffuse
across sharp intensity transitions) was compared against plain Gaussian,
a median filter, and a bilateral filter on synthetic data, measuring both
axes that matter:
  - noise suppression: how many spurious connected components survive
    thresholding a uniformly noisy region right at the threshold value
    (the worst case for _closest_component_to_axis_line's selection logic)
  - thin-gap preservation: how close a true air gap's minimum intensity
    stays to its real value after smoothing, at a couple of gap widths
CurvatureFlow was the only candidate that matched the current Gaussian's
noise suppression (40 vs 37 residual components in the stress test) while
preserving a 0.6mm true gap almost exactly (-700, vs Gaussian's -128 --
uncomfortably close to a typical bone_threshold around 100). Median and
bilateral filters preserved gaps just as well but suppressed noise
noticeably worse (205 and 313 components respectively). About 30x slower
than the Gaussian pass on a realistic crop (~270ms vs ~9ms), which is
immaterial for an explicit "Run Segmentation" click.
"""

from __future__ import annotations
import SimpleITK as sitk

from config import CURVATURE_FLOW_TIME_STEP, CURVATURE_FLOW_ITERATIONS


def smooth_for_thresholding(
    image: sitk.Image,
    time_step: float = CURVATURE_FLOW_TIME_STEP,
    iterations: int = CURVATURE_FLOW_ITERATIONS,
) -> sitk.Image:
    """
    Denoises `image` before thresholding, preserving sharp true intensity
    transitions (thin walls, thin gaps between adjacent bone) far better
    than a plain Gaussian blur would -- see this module's docstring for
    the evidence. See config.py's CURVATURE_FLOW_TIME_STEP/_ITERATIONS for
    tuning guidance.
    """
    return sitk.CurvatureFlow(image, timeStep=time_step, numberOfIterations=iterations)
