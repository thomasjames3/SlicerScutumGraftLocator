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
import SimpleITK as sitk

from config import TARGET_VOXEL_SPACING_MM


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
