"""
segment_dl.py
=============
Stage B segmentation: runs a trained nnU-Net model instead of the
threshold-based Stage A method, once one exists.

The important design decision in this file is `segment()`: it is the single
function the Slicer UI calls, and it transparently decides whether to use
the trained model or fall back to Stage A. This means:

  - A brand-new install with zero banked training cases still works
    immediately (falls back to Stage A every time).
  - Once you've trained a model with training/train_model.py, it starts
    getting used automatically -- no UI changes, no surgeon-visible
    "switch to AI mode" toggle to worry about.
  - If the model directory is present but something goes wrong loading it
    (corrupted files, wrong nnU-Net version, etc.), it fails safe by
    falling back to Stage A rather than crashing the surgeon's session.

This file intentionally does NOT import nnunetv2 at the top level -- that
import is deferred into _load_model() so that everyone who doesn't have
nnU-Net installed (i.e. everyone before the first model is trained) can
still import and use this module without errors.

NOTE for whoever implements _run_model(): the pipeline's output is the bony
wall of the ear canal, not the air-filled lumen (see segment_threshold.py
for the reasoning). Whatever Stage B model gets trained here should be
trained on bone-wall labels, and _run_model() must return a bone-wall label
map, so it stays a drop-in swap for segment_threshold.segment_bone_wall().
"""

from __future__ import annotations
import os
import logging
import SimpleITK as sitk

from config import TRAINED_MODEL_DIR
from core.landmarks import EarCanalLandmarks
from core import segment_threshold

logger = logging.getLogger(__name__)

_cached_predictor = None  # loaded lazily, once, and reused across calls


def segment(
    cropped_image: sitk.Image,
    roi_mask: sitk.Image,
    landmarks: EarCanalLandmarks,
    air_threshold: float = None,
    bone_threshold: float = None,
) -> sitk.Image:
    """
    Main entry point used by the Slicer UI. Tries the trained model first;
    falls back to the bone-wall threshold method
    (segment_threshold.segment_bone_wall) if no model is available or
    loading/running it fails for any reason.

    Note: the output of this function (and of the Stage A fallback) is the
    BONY WALL of the ear canal, not the air-filled lumen -- see
    segment_threshold.py for why the lumen is still used as an internal
    step.

    Parameters mirror segment_threshold.segment_bone_wall -- see that file
    for details. `air_threshold`/`bone_threshold` are only used by the
    Stage A fallback.
    """
    predictor = _get_predictor()

    if predictor is None:
        logger.info("No trained model found -- using threshold-based segmentation.")
        kwargs = _threshold_kwargs(air_threshold, bone_threshold)
        return segment_threshold.segment_bone_wall(
            cropped_image, roi_mask, landmarks, **kwargs
        )

    try:
        return _run_model(predictor, cropped_image, roi_mask)
    except Exception:
        logger.exception(
            "Trained model failed to run; falling back to threshold-based "
            "segmentation for this case."
        )
        kwargs = _threshold_kwargs(air_threshold, bone_threshold)
        return segment_threshold.segment_bone_wall(
            cropped_image, roi_mask, landmarks, **kwargs
        )


def _threshold_kwargs(air_threshold: float = None, bone_threshold: float = None) -> dict:
    """Only pass through threshold overrides that were actually given, so
    segment_bone_wall's own defaults (from config.py) are used otherwise."""
    kwargs = {}
    if air_threshold is not None:
        kwargs["air_threshold"] = air_threshold
    if bone_threshold is not None:
        kwargs["bone_threshold"] = bone_threshold
    return kwargs


def is_trained_model_available() -> bool:
    """
    Lets the UI show a small "Segmentation engine: AI model (v3)" vs.
    "Segmentation engine: rule-based" indicator, if you want that kind of
    transparency for surgeons/QA purposes -- entirely optional, doesn't
    affect behavior.
    """
    return _get_predictor() is not None


def _get_predictor():
    global _cached_predictor
    if _cached_predictor is not None:
        return _cached_predictor

    if not os.path.isdir(TRAINED_MODEL_DIR):
        return None

    _cached_predictor = _load_model(TRAINED_MODEL_DIR)
    return _cached_predictor


def _load_model(model_dir: str):
    """
    Loads a trained nnU-Net predictor. Deferred import (see module
    docstring) -- this only gets called once a model directory actually
    exists, at which point nnunetv2 is expected to be installed.

    TODO once training/train_model.py has produced a first model: fill in
    the actual nnUNetPredictor initialization here. Left as a stub with a
    clear interface so the rest of the pipeline can be built and tested
    with Stage A alone in the meantime.
    """
    try:
        from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor
    except ImportError:
        logger.warning(
            "A trained model directory exists at %s, but nnunetv2 isn't "
            "installed. Run: pip install nnunetv2 --break-system-packages",
            model_dir,
        )
        return None

    # predictor = nnUNetPredictor(...)
    # predictor.initialize_from_trained_model_folder(model_dir, ...)
    # return predictor
    raise NotImplementedError(
        "Stage B model loading is stubbed out until a first model exists. "
        "See training/train_model.py."
    )


def _run_model(predictor, cropped_image: sitk.Image, roi_mask: sitk.Image) -> sitk.Image:
    """
    TODO: run inference with `predictor` on `cropped_image`, restricted to
    `roi_mask`, and return a UInt8 label image with the same geometry as
    cropped_image. Left as a stub alongside _load_model above.
    """
    raise NotImplementedError
