"""
page_pinna_landmarks.py
=========================
Page 5: place the single ear-center point.

Simpler than the scutum's page since the pinna's Stage A only
needs a search region, not an orientation (see core/pinna_landmarks.py for
why). Left/right ear used to be picked here too, via radio buttons, but
that's now decided once up front on the DICOM load page
(WizardState.ear_side) -- see that field's docstring in wizard_state.py
for why a single, earlier source of truth replaced this page's own copy.

Expected widgets in page_pinna_landmarks.ui:
  - instructionLabel     (QLabel)
  - tutorialLabel        (QLabel) -- extra guidance, shown only in tutorial mode
  - placePointButton     (QPushButton)
  - resetButton          (QPushButton) -- this page only
  - revertToHereButton   (QPushButton) -- clear every later step, keep this point
  - statusLabel          (QLabel)
"""

from __future__ import annotations
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from EarReconstructionPlannerLib import wizard_state
from core.pinna_landmarks import PinnaLandmarks, PINNA_LANDMARK_STEP


class PinnaLandmarksPage(WizardPage):
    def on_enter(self):
        import slicer

        # Reuse a still-live node from an earlier visit instead of always
        # creating a new one -- see page_scutum_landmarks.py's on_enter for
        # why (avoids orphaning nodes across Back/Next navigation).
        existing = self.state.pinna_landmarks_fiducial_node
        if existing is not None and slicer.mrmlScene.IsNodePresent(existing):
            self._fiducial_node = existing
        else:
            self._fiducial_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode", "PinnaCenter"
            )
            self._fiducial_node.CreateDefaultDisplayNodes()
            self.state.pinna_landmarks_fiducial_node = self._fiducial_node
        self._fiducial_node.GetDisplayNode().SetVisibility(True)

        self.ui.instructionLabel.setText(PINNA_LANDMARK_STEP["instruction"])
        self.set_tutorial_text(
            "This point just gives the tool a rough search area for the "
            "pinna, so it doesn't need to be precise. Nothing pinna-related "
            "is in the 3D view yet -- the only model currently shown is the "
            "isolated scutum defect patch from the previous stage (ignore "
            "it here; the pinna's own model isn't created until the next "
            "page). Instead, use the 2D slice views (Red/Yellow/Green): "
            "hover over one and scroll the mouse wheel to move through "
            "slices (hold the middle mouse button and drag to pan) until "
            "you can see the ear on the scan, then click 'Place Point' "
            "below and left-click once, roughly in the middle of the pinna "
            "(e.g. inside the concha, the bowl-shaped hollow). You can "
            "re-click to move it if you're not happy with where it landed."
        )
        self.ui.placePointButton.clicked.connect(self._on_place_point_clicked)
        self.ui.resetButton.clicked.connect(self._on_reset_clicked)
        self.ui.revertToHereButton.clicked.connect(self._on_revert_to_here_clicked)

        self.ui.statusLabel.setText("")

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
        self.ui.statusLabel.setText("Click roughly the center of the pinna...")

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
        self.ui.statusLabel.setText("")

    def _on_revert_to_here_clicked(self):
        import slicer

        if wizard_state.has_downstream_state(self.state, "pinna_landmarks"):
            if not slicer.util.confirmYesNoDisplay(
                "This will clear every step after this one (the pinna "
                "segmentation and outline, verification, and the heatmap "
                "result). This point is kept. Continue?"
            ):
                return
            wizard_state.clear_downstream_state(self.state, "pinna_landmarks")
        self.ui.statusLabel.setText(
            "Later steps cleared. Go to Next when ready to redo them."
        )

    def on_leave_next(self):
        warning = self.state.pinna_landmarks.validate()
        if warning:
            return False, warning
        return True, ""
