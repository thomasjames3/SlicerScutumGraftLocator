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


def save_label_map(label_image: sitk.Image, path: str) -> None:
    """Save a segmentation label map to disk (.nrrd recommended)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sitk.WriteImage(label_image, path)
