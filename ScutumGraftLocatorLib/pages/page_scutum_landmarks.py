"""
page_scutum_landmarks.py
==========================
Page 2: place the 2 ear canal landmarks, one at a time.

Uses Slicer's own Markups fiducial placement (the same click-to-place
interaction surgeons already know from other Slicer tools) rather than a
custom picking widget. Each of the 2 steps in core/landmarks.py's
LANDMARK_STEPS is shown one at a time with its plain-language instruction;
after each click, the point is captured and the wizard automatically
advances to the next one.

Expected widgets in page_scutum_landmarks.ui:
  - instructionLabel   (QLabel)  -- shows the current step's plain instruction
  - tutorialLabel      (QLabel)  -- extra guidance, shown only in tutorial mode
  - stepProgressLabel  (QLabel)  -- e.g. "Point 1 of 2"
  - placePointButton   (QPushButton)
  - resetButton        (QPushButton) -- start over from point 1 (this page only)
  - revertToHereButton (QPushButton) -- clear every later step, keep these points
  - statusLabel        (QLabel)
"""

from __future__ import annotations
from ScutumGraftLocatorLib.pages.base_page import WizardPage
from ScutumGraftLocatorLib import wizard_state
from core.landmarks import LANDMARK_STEPS

# Extra step-by-step guidance shown only in tutorial mode, keyed by the same
# "field" name LANDMARK_STEPS uses -- kept here (not in core/landmarks.py)
# since it's Slicer-UI-specific wording, not part of the landmark data model.
_TUTORIAL_TEXT_BY_FIELD = {
    "canal_opening": (
        "Place this point at the opening of the bony ear canal. It's "
        "easiest to find in a slice view (Red, Yellow, or Green): hover "
        "over a slice view and scroll the mouse wheel to step through "
        "slices until you can clearly see it, then click 'Place Point' "
        "below and left-click once, right on that spot. You can pan a "
        "slice view by holding the middle mouse button and dragging, and "
        "zoom in first (scroll while holding Ctrl, or right-click-drag) to "
        "place it more precisely.\n\n"
        "Doesn't need to be pixel-perfect -- this point only sets up a "
        "rough search region; the actual bone/air boundary is found "
        "automatically on the next page."
    ),
    "near_eardrum": (
        "This second point marks the inner end of the canal, just before "
        "the eardrum. Together with the first point, it defines the canal's "
        "direction, so the tool knows which way to search.\n\n"
        "Same approach as before: scroll through slices in a 2D view to find "
        "the right spot, then click 'Place Point' and left-click once there. "
        "If you place a point in the wrong spot, use 'Reset All Points' "
        "below and start over."
    ),
}


class ScutumLandmarksPage(WizardPage):
    def on_enter(self):
        import slicer

        # Reuse the fiducial node from a previous visit if it's still live
        # (e.g. the surgeon went Next then Back) instead of always creating
        # a fresh one -- creating a new node on every re-entry is what made
        # "Reset All Points" look broken: it only ever cleared whichever
        # node happened to be current, while earlier visits' nodes were
        # silently orphaned in the scene and the step counter always
        # restarted at 0 regardless of points already placed.
        existing = self.state.scutum_landmarks_fiducial_node
        if existing is not None and slicer.mrmlScene.IsNodePresent(existing):
            self._fiducial_node = existing
        else:
            self._fiducial_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode", "ScutumLandmarks"
            )
            self._fiducial_node.SetLocked(False)
            self._fiducial_node.CreateDefaultDisplayNodes()
            self.state.scutum_landmarks_fiducial_node = self._fiducial_node
        self._fiducial_node.GetDisplayNode().SetVisibility(True)

        # Resume at whichever step matches what's already been placed,
        # instead of always assuming this is a first visit.
        self._current_step = 0
        for step in LANDMARK_STEPS:
            if getattr(self.state.scutum_landmarks, step["field"]) is None:
                break
            self._current_step += 1

        self.ui.placePointButton.clicked.connect(self._on_place_point_clicked)
        self.ui.resetButton.clicked.connect(self._on_reset_clicked)
        self.ui.revertToHereButton.clicked.connect(self._on_revert_to_here_clicked)

        self._observer_tag = None
        self._update_step_display()

    def _update_step_display(self):
        if self._current_step >= len(LANDMARK_STEPS):
            self.ui.instructionLabel.setText(f"All {len(LANDMARK_STEPS)} points placed.")
            self.ui.stepProgressLabel.setText(f"{len(LANDMARK_STEPS)} of {len(LANDMARK_STEPS)}")
            self.ui.placePointButton.setEnabled(False)
            self.ui.statusLabel.setText("Click Next to continue, or Reset to redo the points.")
            self.set_tutorial_text(
                "Both points are placed. Check them in the 3D or slice views "
                "-- if either looks wrong, use 'Reset All Points' below to "
                "start over, or 'Revert to Here' if you'd only redo later "
                "steps. Otherwise, click Next."
            )
        else:
            step = LANDMARK_STEPS[self._current_step]
            self.ui.instructionLabel.setText(step["instruction"])
            self.ui.stepProgressLabel.setText(
                f"{self._current_step + 1} of {len(LANDMARK_STEPS)}"
            )
            self.ui.placePointButton.setEnabled(True)
            self.ui.statusLabel.setText("")
            self.set_tutorial_text(_TUTORIAL_TEXT_BY_FIELD.get(step["field"], ""))

    def _on_place_point_clicked(self):
        import slicer

        interaction_node = slicer.app.applicationLogic().GetInteractionNode()
        selection_node = slicer.app.applicationLogic().GetSelectionNode()
        selection_node.SetActivePlaceNodeID(self._fiducial_node.GetID())
        interaction_node.SetCurrentInteractionMode(interaction_node.Place)
        interaction_node.SetPlaceModePersistence(0)  # one point, then back to normal mode

        # Capture the point once it's placed, rather than polling.
        self._observer_tag = self._fiducial_node.AddObserver(
            self._fiducial_node.PointPositionDefinedEvent, self._on_point_placed
        )
        self.ui.placePointButton.setEnabled(False)
        self.ui.statusLabel.setText("Click a point in the 3D view or on a slice...")

    def _on_point_placed(self, caller, event):
        if self._observer_tag is not None:
            self._fiducial_node.RemoveObserver(self._observer_tag)
            self._observer_tag = None

        point_index = self._fiducial_node.GetNumberOfControlPoints() - 1
        ras = [0.0, 0.0, 0.0]
        self._fiducial_node.GetNthControlPointPositionWorld(point_index, ras)

        field_name = LANDMARK_STEPS[self._current_step]["field"]
        setattr(self.state.scutum_landmarks, field_name, tuple(ras))
        self._fiducial_node.SetNthControlPointLabel(point_index, field_name)

        self._current_step += 1
        self._update_step_display()

    def _on_reset_clicked(self):
        self._fiducial_node.RemoveAllControlPoints()
        self._current_step = 0
        from core.landmarks import EarCanalLandmarks
        self.state.scutum_landmarks = EarCanalLandmarks()
        self._update_step_display()

    def _on_revert_to_here_clicked(self):
        import slicer

        if wizard_state.has_downstream_state(self.state, "scutum_landmarks"):
            if not slicer.util.confirmYesNoDisplay(
                "This will clear every step after this one (the pinna "
                "steps, the scutum segmentation and outline, verification, "
                "and the heatmap result). These 2 points are kept. Continue?"
            ):
                return
            wizard_state.clear_downstream_state(self.state, "scutum_landmarks")
        self.ui.statusLabel.setText(
            "Later steps cleared. Go to Next when ready to redo them."
        )

    def on_leave_next(self):
        warning = self.state.scutum_landmarks.validate()
        if warning:
            return False, warning
        return True, ""
