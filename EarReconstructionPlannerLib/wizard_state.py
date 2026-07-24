"""
wizard_state.py
================
A single shared object holding everything collected across the wizard's
pages -- the loaded scan, both sets of landmarks, intermediate
segmentations, and final mesh file paths. Every page reads from and writes
to one instance of this, so information collected early (e.g. the scan
volume on page 2) is still available at the very last page (the curvature
comparison), without pages needing to know about each other directly.

This also serves as a plain-language map of the whole wizard workflow --
reading the field list below tells you exactly what data flows through
the wizard, in order. (There's also a tutorial-only "welcome" page with no
state of its own -- see PAGE_ORDER/SKIP_PAGE_IF below.)
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
    # Which ear this case is for ("left"/"right"). Decided once here, up
    # front, and read by every later page that needs it (the pinna
    # landmarks page no longer asks separately, and the 3D view's
    # recenter/orient-camera helper uses it too -- see
    # base_page.WizardPage.recenter_3d_view()). A single source of truth
    # instead of a per-page field prevents the two ever disagreeing.
    ear_side: Optional[str] = None

    # --- Pages 2-4: Scutum (ear canal bone wall + defect outline) ---
    scutum_landmarks: EarCanalLandmarks = field(default_factory=EarCanalLandmarks)
    scutum_landmarks_fiducial_node = None  # vtkMRMLMarkupsFiducialNode, hidden once drawing starts
    scutum_bone_wall_label_node = None  # vtkMRMLLabelMapVolumeNode
    scutum_bone_wall_model_node = None  # vtkMRMLModelNode, loaded into the scene for drawing on
    scutum_bone_wall_mesh_path: Optional[str] = None
    scutum_defect_mesh_path: Optional[str] = None  # after the drawn-outline isolation step
    scutum_defect_model_node = None  # vtkMRMLModelNode for the isolated defect patch

    # --- Pages 5-7: Pinna ---
    pinna_landmarks: PinnaLandmarks = field(default_factory=PinnaLandmarks)
    pinna_landmarks_fiducial_node = None  # vtkMRMLMarkupsFiducialNode, hidden once drawing starts
    pinna_region_label_node = None  # vtkMRMLLabelMapVolumeNode
    pinna_region_model_node = None  # vtkMRMLModelNode, loaded into the scene for drawing on
    pinna_region_mesh_path: Optional[str] = None
    pinna_isolated_mesh_path: Optional[str] = None  # after the drawn-outline isolation step
    pinna_isolated_model_node = None  # vtkMRMLModelNode for the isolated pinna patch

    # --- Page 8: Verify ---
    surgeon_approved_scutum: bool = False
    surgeon_approved_pinna: bool = False

    # --- Page 9: Curvature comparison ---
    heatmap_output_path: Optional[str] = None  # pinna_heatmap.ply
    heatmap_model_node = None  # vtkMRMLModelNode, removed+reloaded on re-run
    ranked_sites_csv_path: Optional[str] = None  # top_harvest_sites.csv

    # --- Misc ---
    working_dir: Optional[str] = None  # scratch folder for intermediate files, set on Setup page
    # Chosen once on the Setup page (default Normal). When True, every page
    # shows an extra `tutorialLabel` widget with detailed, plain-language
    # guidance -- including how to use general Slicer features (navigating
    # slices, rotating the 3D view, the curve-drawing tool) -- alongside its
    # normal instructions. See base_page.WizardPage.set_tutorial_text().
    tutorial_mode: bool = False


# The fixed sequence of pages, in order. Each entry is
# (page_id, ui_filename, controller_class_name) -- used by the main module
# widget to build the QStackedWidget and load the matching controller for
# each page. Keeping this list in one place means reordering or adding a
# page later is a one-line change here, not a hunt through the main widget
# file.
PAGE_ORDER = [
    ("setup", "page_setup.ui", "SetupPage"),
    ("welcome", "page_welcome.ui", "WelcomePage"),
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

# Which WizardState fields each page is responsible for populating. This is
# the single source of truth for "what depends on what" used by
# clear_page_state()/clear_downstream_state() below -- every page's
# "Reset This Page" / "Revert to Here" button (see page_*.py) goes through
# these instead of each page hand-rolling its own cleanup list, so the
# dependency chain only has to be gotten right in one place.
PAGE_OWNED_FIELDS = {
    "dicom_load": ["volume_node", "ear_side"],
    "scutum_landmarks": ["scutum_landmarks", "scutum_landmarks_fiducial_node"],
    "scutum_review": [
        "scutum_bone_wall_label_node",
        "scutum_bone_wall_model_node",
        "scutum_bone_wall_mesh_path",
    ],
    "scutum_draw": ["scutum_defect_mesh_path", "scutum_defect_model_node"],
    "pinna_landmarks": ["pinna_landmarks", "pinna_landmarks_fiducial_node"],
    "pinna_review": [
        "pinna_region_label_node",
        "pinna_region_model_node",
        "pinna_region_mesh_path",
    ],
    "pinna_draw": ["pinna_isolated_mesh_path", "pinna_isolated_model_node"],
    "verify": ["surgeon_approved_scutum", "surgeon_approved_pinna"],
    "curvature": ["heatmap_output_path", "heatmap_model_node", "ranked_sites_csv_path"],
}

# Factories for what an owned field resets to. Any field not listed here
# just resets to None (covers every mesh path / model node / label node).
_FIELD_RESET_DEFAULTS = {
    "scutum_landmarks": EarCanalLandmarks,
    "pinna_landmarks": PinnaLandmarks,
    "surgeon_approved_scutum": lambda: False,
    "surgeon_approved_pinna": lambda: False,
}


# Optional per-page predicate: page_id -> callable(state) -> True if this
# page should be skipped entirely for the current configuration (e.g. the
# tutorial-only welcome page when Normal mode was chosen on Setup). Kept
# here as plain state checks -- NOT as a method on the controller class --
# so the main module can decide whether to skip a page without importing
# and instantiating that page's controller module, preserving the lazy
# per-page import pattern described in EarReconstructionPlanner.py's
# _get_or_create_controller().
SKIP_PAGE_IF = {
    "welcome": lambda state: not state.tutorial_mode,
}


def should_skip_page(state: "WizardState", page_id: str) -> bool:
    predicate = SKIP_PAGE_IF.get(page_id)
    return predicate(state) if predicate else False


def _page_index(page_id: str) -> int:
    for i, (pid, _, _) in enumerate(PAGE_ORDER):
        if pid == page_id:
            return i
    raise ValueError(f"Unknown page_id: {page_id!r}")


def clear_page_state(state: "WizardState", page_id: str) -> None:
    """Reset every field `page_id` owns back to its default, removing any
    MRML node currently referenced by those fields from the scene first.

    Node values are recognized by duck-typing (`hasattr(value, "GetID")`)
    rather than importing vtkMRML*-specific types, and `slicer` itself is
    imported lazily here so this module stays importable outside Slicer
    (matching core/'s "no hard Slicer dependency" pattern, even though
    WizardState's fields are Slicer node references in practice).
    """
    import slicer

    for field_name in PAGE_OWNED_FIELDS.get(page_id, []):
        value = getattr(state, field_name)
        if value is not None and hasattr(value, "GetID"):
            try:
                if slicer.mrmlScene.IsNodePresent(value):
                    slicer.mrmlScene.RemoveNode(value)
            except Exception:
                pass  # node already gone / scene already torn down
        default_factory = _FIELD_RESET_DEFAULTS.get(field_name, lambda: None)
        setattr(state, field_name, default_factory())


def clear_downstream_state(state: "WizardState", from_page_id: str) -> None:
    """Clear every page's owned state *after* `from_page_id` (exclusive) --
    used by "Revert to Here" (keeps this page's own result, wipes
    everything that was built on top of it) and by review pages'
    auto-invalidation on re-run (see page_scutum_review.py /
    page_pinna_review.py)."""
    start = _page_index(from_page_id) + 1
    for page_id, _, _ in PAGE_ORDER[start:]:
        clear_page_state(state, page_id)


def has_downstream_state(state: "WizardState", from_page_id: str) -> bool:
    """True if any page after `from_page_id` currently owns non-default
    state -- used to skip the confirmation prompt when there's nothing to
    lose."""
    start = _page_index(from_page_id) + 1
    for page_id, _, _ in PAGE_ORDER[start:]:
        for field_name in PAGE_OWNED_FIELDS.get(page_id, []):
            value = getattr(state, field_name)
            default_factory = _FIELD_RESET_DEFAULTS.get(field_name, lambda: None)
            default = default_factory()
            if value != default:
                return True
    return False
