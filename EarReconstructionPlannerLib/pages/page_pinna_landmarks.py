"""
page_pinna_landmarks.py
=========================
Page 5: place the single ear-center point and specify left/right ear.

Much simpler than the scutum's 4-point page since the pinna's Stage A only
needs a search region, not an orientation (see core/pinna_landmarks.py for
why). The side is picked from radio buttons rather than inferred, since a
silent wrong guess here would be a bad failure mode.

Expected widgets in page_pinna_landmarks.ui:
  - instructionLabel     (QLabel)
  - leftRadioButton      (QRadioButton)
  - rightRadioButton     (QRadioButton)
  - placePointButton     (QPushButton)
  - resetButton          (QPushButton)
  - statusLabel          (QLabel)
"""

from __future__ import annotations
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from core.pinna_landmarks import PinnaLandmarks, PINNA_LANDMARK_STEP


class PinnaLandmarksPage(WizardPage):
    def on_enter(self):
        import slicer

        self._fiducial_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLMarkupsFiducialNode", "PinnaCenter"
        )

        self.ui.instructionLabel.setText(PINNA_LANDMARK_STEP["instruction"])
        self.ui.placePointButton.clicked.connect(self._on_place_point_clicked)
        self.ui.resetButton.clicked.connect(self._on_reset_clicked)
        self.ui.leftRadioButton.toggled.connect(self._on_side_changed)
        self.ui.rightRadioButton.toggled.connect(self._on_side_changed)
        self.ui.statusLabel.setText("")

    def _on_side_changed(self):
        if self.ui.leftRadioButton.isChecked():
            self.state.pinna_landmarks.side = "left"
        elif self.ui.rightRadioButton.isChecked():
            self.state.pinna_landmarks.side = "right"

    def _on_place_point_clicked(self):
        import slicer

        interaction_node = slicer.app.applicationLogic().GetInteractionNode()
        selection_node = slicer.app.applicationLogic().GetSelectionNode()
        selection_node.SetActivePlaceNodeID(self._fiducial_node.GetID())
        interaction_node.SetCurrentInteractionMode(interaction_node.Place)
        interaction_node.SetPlaceModePersistence(0)

        self._observer_tag = self._fiducial_node.AddObserver(
            self._fiducial_node.PointPositionDefinedEvent, self._on_point_placed
        )
        self.ui.placePointButton.setEnabled(False)
        self.ui.statusLabel.setText("Click roughly the center of the ear...")

    def _on_point_placed(self, caller, event):
        self._fiducial_node.RemoveObserver(self._observer_tag)
        ras = [0.0, 0.0, 0.0]
        self._fiducial_node.GetNthControlPointPositionWorld(0, ras)
        self.state.pinna_landmarks.ear_center = tuple(ras)
        self.ui.placePointButton.setEnabled(True)
        self.ui.statusLabel.setText("Point placed. You can re-click to move it if needed.")

    def _on_reset_clicked(self):
        self._fiducial_node.RemoveAllControlPoints()
        self.state.pinna_landmarks = PinnaLandmarks()
        self.ui.leftRadioButton.setChecked(False)
        self.ui.rightRadioButton.setChecked(False)
        self.ui.statusLabel.setText("")

    def on_leave_next(self):
        warning = self.state.pinna_landmarks.validate()
        if warning:
            return False, warning
        return True, ""
