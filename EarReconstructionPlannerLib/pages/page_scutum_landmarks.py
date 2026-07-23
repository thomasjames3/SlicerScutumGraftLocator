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
  - stepProgressLabel  (QLabel)  -- e.g. "Point 1 of 2"
  - placePointButton   (QPushButton)
  - resetButton        (QPushButton) -- start over from point 1
  - statusLabel        (QLabel)
"""

from __future__ import annotations
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from core.landmarks import LANDMARK_STEPS


class ScutumLandmarksPage(WizardPage):
    def on_enter(self):
        import slicer

        self._fiducial_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLMarkupsFiducialNode", "ScutumLandmarks"
        )
        self._fiducial_node.SetLocked(False)
        self._fiducial_node.CreateDefaultDisplayNodes()
        self._fiducial_node.GetDisplayNode().SetVisibility(True)
        self.state.scutum_landmarks_fiducial_node = self._fiducial_node
        self._current_step = 0

        self.ui.placePointButton.clicked.connect(self._on_place_point_clicked)
        self.ui.resetButton.clicked.connect(self._on_reset_clicked)

        self._observer_tag = None
        self._update_step_display()

    def _update_step_display(self):
        if self._current_step >= len(LANDMARK_STEPS):
            self.ui.instructionLabel.setText(f"All {len(LANDMARK_STEPS)} points placed.")
            self.ui.stepProgressLabel.setText(f"{len(LANDMARK_STEPS)} of {len(LANDMARK_STEPS)}")
            self.ui.placePointButton.setEnabled(False)
            self.ui.statusLabel.setText("Click Next to continue, or Reset to redo the points.")
        else:
            step = LANDMARK_STEPS[self._current_step]
            self.ui.instructionLabel.setText(step["instruction"])
            self.ui.stepProgressLabel.setText(
                f"{self._current_step + 1} of {len(LANDMARK_STEPS)}"
            )
            self.ui.placePointButton.setEnabled(True)
            self.ui.statusLabel.setText("")

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

    def on_leave_next(self):
        warning = self.state.scutum_landmarks.validate()
        if warning:
            return False, warning
        return True, ""
