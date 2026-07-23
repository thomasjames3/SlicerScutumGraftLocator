"""
wizard_state.py
================
A single shared object holding everything collected across the wizard's
pages -- the loaded scan, both sets of landmarks, intermediate
segmentations, and final mesh file paths. Every page reads from and writes
to one instance of this, so information collected early (e.g. the scan
volume on page 2) is still available at the very last page (the curvature
comparison), without pages needing to know about each other directly.

This also serves as a plain-language map of the whole 10-page workflow --
reading the field list below tells you exactly what data flows through
the wizard, in order.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from core.landmarks import EarCanalLandmarks
from core.pinna_landmarks import PinnaLandmarks


@dataclass
class WizardState:
    # --- Page 1: DICOM load ---
    volume_node = None  # vtkMRMLScalarVolumeNode, the loaded/resampled scan

    # --- Pages 2-4: Scutum (ear canal bone wall + defect outline) ---
    scutum_landmarks: EarCanalLandmarks = field(default_factory=EarCanalLandmarks)
    scutum_bone_wall_label_node = None  # vtkMRMLLabelMapVolumeNode
    scutum_bone_wall_model_node = None  # vtkMRMLModelNode, loaded into the scene for drawing on
    scutum_bone_wall_mesh_path: Optional[str] = None
    scutum_defect_mesh_path: Optional[str] = None  # after the drawn-outline isolation step

    # --- Pages 5-7: Pinna ---
    pinna_landmarks: PinnaLandmarks = field(default_factory=PinnaLandmarks)
    pinna_region_label_node = None  # vtkMRMLLabelMapVolumeNode
    pinna_region_model_node = None  # vtkMRMLModelNode, loaded into the scene for drawing on
    pinna_region_mesh_path: Optional[str] = None
    pinna_isolated_mesh_path: Optional[str] = None  # after the drawn-outline isolation step

    # --- Page 8: Verify ---
    surgeon_approved_scutum: bool = False
    surgeon_approved_pinna: bool = False

    # --- Page 9: Curvature comparison ---
    heatmap_output_path: Optional[str] = None

    # --- Misc ---
    working_dir: Optional[str] = None  # scratch folder for intermediate files, set on Setup page


# The fixed sequence of pages, in order. Each entry is
# (page_id, ui_filename, controller_class_name) -- used by the main module
# widget to build the QStackedWidget and load the matching controller for
# each page. Keeping this list in one place means reordering or adding a
# page later is a one-line change here, not a hunt through the main widget
# file.
PAGE_ORDER = [
    ("setup", "page_setup.ui", "SetupPage"),
    ("dicom_load", "page_dicom_load.ui", "DicomLoadPage"),
    ("scutum_landmarks", "page_scutum_landmarks.ui", "ScutumLandmarksPage"),
    ("scutum_review", "page_scutum_review.ui", "ScutumReviewPage"),
    ("scutum_draw", "page_scutum_draw.ui", "ScutumDrawPage"),
    ("pinna_landmarks", "page_pinna_landmarks.ui", "PinnaLandmarksPage"),
    ("pinna_review", "page_pinna_review.ui", "PinnaReviewPage"),
    ("pinna_draw", "page_pinna_draw.ui", "PinnaDrawPage"),
    ("verify", "page_verify.ui", "VerifyPage"),
    ("curvature", "page_curvature.ui", "CurvaturePage"),
]
