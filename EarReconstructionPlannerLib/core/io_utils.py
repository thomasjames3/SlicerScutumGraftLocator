"""
io_utils.py
===========
Loading scans in, resampling them to a consistent spacing, and basic saving.

Uses SimpleITK because it handles DICOM series (a folder of .dcm files) and
NRRD/NIfTI volumes with the same API, and it's what 3D Slicer itself is
built on -- so a SimpleITK image can be handed back and forth with Slicer's
own volume objects with minimal glue code (see slicer_module/).
"""

from __future__ import annotations
import os
import numpy as np
import SimpleITK as sitk

from config import TARGET_VOXEL_SPACING_MM

_RAS_LPS_FLIP = np.diag([-1.0, -1.0, 1.0])


def flip_ras_lps(image: sitk.Image) -> sitk.Image:
    """
    Returns a copy of `image` with its origin/direction converted between
    Slicer's native RAS convention and plain ITK/SimpleITK's LPS
    convention (negating X and Y). This conversion is its own inverse, so
    the same function converts in either direction.

    Why this exists: `sitkUtils.PullVolumeFromSlicer()` converts a Slicer
    volume node's geometry from RAS to LPS before building the sitk.Image
    (and `PushVolumeToSlicer()` converts back), matching plain
    ITK/DICOM convention. But every surgeon-placed landmark in this
    project (canal_opening, near_eardrum, ear_center, ...) is captured
    directly from Slicer's Markups nodes in RAS -- Slicer's own
    convention. Every physical-coordinate calculation in core/
    (roi_crop.py, segment_threshold.py, mesh_export.py, ...) is written
    assuming landmarks and image geometry share the same convention. Call
    this right after PullVolumeFromSlicer() (LPS -> RAS) before doing any
    of that math, and again right before PushVolumeToSlicer() (RAS ->
    LPS) -- see page_scutum_review.py / page_pinna_review.py for the
    pattern. Skipping this silently offsets every ROI/axis calculation by
    a mirror flip across the sagittal and coronal planes, since RAS and
    LPS only differ in the sign of X (Right/Left) and Y (Anterior/
    Posterior).
    """
    flipped = sitk.Image(image)
    origin = _RAS_LPS_FLIP @ np.array(image.GetOrigin())
    direction = _RAS_LPS_FLIP @ np.array(image.GetDirection()).reshape(3, 3)
    flipped.SetOrigin(tuple(origin))
    flipped.SetDirection(tuple(direction.flatten()))
    return flipped


def load_volume(path: str) -> sitk.Image:
    """
    Load a scan from either a DICOM series folder or a single volume file
    (.nrrd, .nii, .nii.gz, .mha, ...).

    Parameters
    ----------
    path : str
        Either a folder containing a DICOM series, or a path to a single
        volume file.

    Returns
    -------
    sitk.Image
        The loaded volume, un-modified (not yet resampled).
    """
    if os.path.isdir(path):
        reader = sitk.ImageSeriesReader()
        dicom_names = reader.GetGDCMSeriesFileNames(path)
        if not dicom_names:
            raise ValueError(
                f"No DICOM series found in folder: {path}. "
                "Check that this folder directly contains .dcm files."
            )
        reader.SetFileNames(dicom_names)
        return reader.Execute()

    if os.path.isfile(path):
        return sitk.ReadImage(path)

    raise FileNotFoundError(f"Could not find scan at: {path}")


def resample_to_isotropic(
    image: sitk.Image,
    spacing_mm: float = TARGET_VOXEL_SPACING_MM,
    is_label: bool = False,
) -> sitk.Image:
    """
    Resample a volume to isotropic voxel spacing.

    Why this matters: raw clinical scans often have different in-plane vs.
    slice-thickness spacing (e.g. 0.2 x 0.2 x 0.5 mm), which distorts
    distances and makes thresholds/measurements inconsistent between scans.
    Resampling to a single isotropic spacing up front means every later
    step (thresholding, connected components, mesh export) behaves the
    same way regardless of how a given scan was originally acquired.

    Parameters
    ----------
    image : sitk.Image
        The volume to resample.
    spacing_mm : float
        Target spacing in millimeters, applied equally to all 3 axes.
    is_label : bool
        Set True when resampling a segmentation/label map rather than the
        original grayscale scan -- this switches to nearest-neighbor
        interpolation so label values don't get blurred into invalid
        in-between numbers.

    Returns
    -------
    sitk.Image
        The resampled volume.
    """
    original_spacing = image.GetSpacing()
    original_size = image.GetSize()

    new_spacing = [spacing_mm] * 3
    new_size = [
        int(round(original_size[i] * (original_spacing[i] / spacing_mm)))
        for i in range(3)
    ]

    resampler = sitk.ResampleImageFilter()
    resampler.SetOutputSpacing(new_spacing)
    resampler.SetSize(new_size)
    resampler.SetOutputDirection(image.GetDirection())
    resampler.SetOutputOrigin(image.GetOrigin())
    resampler.SetTransform(sitk.Transform())
    resampler.SetDefaultPixelValue(image.GetPixelIDValue())
    resampler.SetInterpolator(
        sitk.sitkNearestNeighbor if is_label else sitk.sitkBSpline
    )

    return resampler.Execute(image)


def resample_to_bounded_anisotropy(
    image: sitk.Image, max_ratio: float, is_label: bool = False
) -> sitk.Image:
    """
    Upsamples the coarsest axis/axes of `image` so no axis's spacing
    exceeds max_ratio times the finest axis's spacing, giving downstream
    steps (thresholding, connected components, morphology, marching_cubes)
    a better-conditioned grid. Only a TRIGGER, not a fixed target: once an
    image is anisotropic enough to trigger this at all, the coarse
    axis/axes are resampled all the way down to match the finest axis
    (full isotropy), not merely down to max_ratio -- confirmed on a real
    scan (2026-08-22/25) that stopping at the trigger ratio alone still
    let marching_cubes tear a real fold off as its own small watertight
    mesh fragment, leaving a genuine hole behind in the main mesh. No-ops
    (returns `image` unchanged) if already within the ratio.

    Deliberately NOT the same as resample_to_isotropic() above (which
    always resamples every scan to one fixed absolute spacing): Thomas
    recalled an earlier version of this pipeline that always resampled
    every scan and got WORSE results, and this project has separately hit
    real performance regressions from assuming one fixed working
    resolution regardless of a scan's actual native spacing (see
    CLAUDE.md "Pinna segmentation performance" -- the sheetness feature's
    multi-round scale-calibration saga hit exactly this trap). This
    function only ever intervenes on a scan that's ALREADY badly
    anisotropic -- an already-near-isotropic scan is returned completely
    unchanged, so nothing about behavior on a normal scan changes.

    Root cause this addresses (confirmed on a real pinna scan,
    2026-08-22/25): a 0.39/0.39/2.5mm scan (~6.4:1 Z:XY ratio, only 32
    slices) reliably tore a genuinely single-connected voxel mask apart
    somewhere in the pipeline. Fixing this only at the very last step
    (mesh_export.label_map_to_mesh's own marching_cubes call) was NOT
    enough on its own, because thresholding/connected-components/spike-
    removal/postprocess's own voxel-radius-based morphology all still ran
    on the badly-anisotropic native grid before that late fix ever got a
    chance to help -- confirmed directly when Thomas manually resampled
    the whole scan to isotropic BEFORE running the pipeline and got a
    correct result. Callers should call this as early as possible in each
    pipeline flow: on the cropped grayscale volume before thresholding
    for a fresh segmentation, or on an already-binary hand-edited mask
    before postprocessing when re-deriving from a Segment Editor edit.

    Parameters
    ----------
    image : sitk.Image
        Grayscale intensity data or a binary label mask -- see is_label.
    max_ratio : float
        Trigger threshold: only fires if the coarsest axis's spacing is
        more than this many times the finest axis's spacing.
    is_label : bool
        True for a binary label mask -- casts to float, resamples with
        linear interpolation, then re-thresholds at 0.5 (the standard
        sub-voxel-accurate way to resample a mask, avoids nearest-
        neighbor's blockier result). False (default) for grayscale
        intensity data -- plain linear interpolation, cast back to the
        input's own pixel type.
    """
    spacing = image.GetSpacing()
    min_spacing = min(spacing)
    if max(spacing) / min_spacing <= max_ratio:
        return image

    new_spacing = tuple(min(s, min_spacing) for s in spacing)
    old_size = image.GetSize()
    new_size = tuple(
        max(1, int(round(old_size[i] * spacing[i] / new_spacing[i]))) for i in range(3)
    )
    print(
        f"[pinna diag] resample_to_bounded_anisotropy: spacing {spacing} -> {new_spacing}, "
        f"size {old_size} -> {new_size}"
    )

    resampler = sitk.ResampleImageFilter()
    resampler.SetOutputSpacing(new_spacing)
    resampler.SetSize(new_size)
    resampler.SetOutputOrigin(image.GetOrigin())
    resampler.SetOutputDirection(image.GetDirection())
    resampler.SetInterpolator(sitk.sitkLinear)
    resampled_float = resampler.Execute(sitk.Cast(image, sitk.sitkFloat32))
    if is_label:
        return sitk.Cast(resampled_float > 0.5, sitk.sitkUInt8)
    return sitk.Cast(resampled_float, image.GetPixelID())


def save_label_map(label_image: sitk.Image, path: str) -> None:
    """Save a segmentation label map to disk (.nrrd recommended)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sitk.WriteImage(label_image, path)
