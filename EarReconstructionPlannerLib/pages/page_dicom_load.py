"""
page_dicom_load.py
====================
Page 1: confirm which loaded scan to use.

Loading the actual DICOM series is NOT reimplemented here -- Slicer's own
DICOM module (the same one surgeons already know from every other Slicer
workflow) is what surgeons use to bring the scan in, exactly as you
described in your workflow: "the surgeon loads their DICOM file as usual."
This page just asks them to pick which already-loaded volume to use for
the rest of the wizard and stores it in the shared wizard state.

Deliberately NOT resampling to isotropic spacing here (or anywhere on the
full scan): every downstream step (roi_crop.py, segment_threshold.py,
segment_pinna_threshold.py, postprocess.py, mesh_export.py) already reads
physical spacing/origin/direction off the image and works correctly on
anisotropic voxels -- none of the thresholding, dilation, or mesh export
math assumes isotropic spacing. Resampling the *entire* scan to a fine
isotropic spacing (e.g. 0.3mm) up front was pure interpolation with no new
information to add, and forcing it across every slice (rather than just a
small cropped ROI, if it were ever needed) is what made bone edges look
blurred/less defined in the 3D view. So this page now just uses the scan
as Slicer loaded it. (io_utils.resample_to_isotropic still exists, kept
for potential future use e.g. normalizing banked training cases, but nothing
in the live pipeline calls it.)

Expected widgets in page_dicom_load.ui:
  - instructionLabel     (QLabel)
  - tutorialLabel        (QLabel) -- extra guidance, shown only in tutorial mode
  - volumeSelector       (qMRMLNodeComboBox, filtered to vtkMRMLScalarVolumeNode)
  - sideLabel            (QLabel)
  - leftRadioButton      (QRadioButton) -- which ear this case is for
  - rightRadioButton     (QRadioButton)
  - resampleButton        (QPushButton) -- "Use this scan"
  - statusLabel          (QLabel)

Note on left/right: this used to be picked on the pinna landmarks page
(page_pinna_landmarks.py), local to PinnaLandmarks.side. Moved here so
it's decided once, up front, before anything else in the pipeline needs
it -- stored as WizardState.ear_side (see that field's docstring) rather
than a per-page field, so every later page (the pinna landmarks page no
longer asks at all; the 3D view recenter/orient helper in
base_page.WizardPage.recenter_3d_view()) reads the same single value
instead of each page trusting a copy stayed in sync.
"""

from __future__ import annotations
from EarReconstructionPlannerLib.pages.base_page import WizardPage


class DicomLoadPage(WizardPage):
    def on_enter(self):
        import slicer
        self.set_tutorial_text(
            "If you haven't loaded the scan yet: click the 'DCM' icon in "
            "Slicer's top toolbar (or File > Add Data), then point it at the "
            "folder of DICOM images and load the series. Once it's loaded, "
            "it will appear in the dropdown below.\n\n"
            "To look through the scan before continuing: hover your mouse "
            "over one of the Red/Yellow/Green 2D slice views and scroll the "
            "mouse wheel to move through slices, one at a time. Hold down "
            "the middle mouse button and drag to pan that slice view "
            "around. In the 3D view, left-click-drag rotates, middle-"
            "click-drag (or Shift+left-drag) pans, and scrolling/right-"
            "click-drag zooms.\n\n"
            "If more than one scan is loaded and you're not sure which one "
            "is the right one: go to Slicer's 'Data' module (use the "
            "modules dropdown near the top of the window), find the list "
            "of volumes there, and click the eyeball icon next to each one "
            "to toggle it visible in the slice views -- this lets you check "
            "each scan in turn to see which is correct. Once you know which "
            "one you need, come back to this module (the modules dropdown "
            "again) and select that same scan from the dropdown below.\n\n"
            "Pick the scan from the dropdown, choose which ear this case is "
            "for, then click 'Use this scan'."
        )
        self.ui.volumeSelector.setMRMLScene(slicer.mrmlScene)
        self.ui.resampleButton.clicked.connect(self._on_use_scan_clicked)
        self.ui.leftRadioButton.toggled.connect(self._on_side_changed)
        self.ui.rightRadioButton.toggled.connect(self._on_side_changed)

        # Restore the side radio buttons from state -- so navigating back
        # to this page doesn't lose a previously made selection.
        if self.state.ear_side == "left":
            self.ui.leftRadioButton.setChecked(True)
        elif self.state.ear_side == "right":
            self.ui.rightRadioButton.setChecked(True)

        if not slicer.mrmlScene.GetNodesByClass("vtkMRMLScalarVolumeNode").GetNumberOfItems():
            self.ui.statusLabel.setText(
                "No volumes loaded yet. Use Slicer's DICOM module (or File > "
                "Add Data) to load a scan first, then come back to this page "
                "and select it below."
            )
        else:
            self.ui.statusLabel.setText("")

    def _on_side_changed(self):
        if self.ui.leftRadioButton.isChecked():
            self.state.ear_side = "left"
        elif self.ui.rightRadioButton.isChecked():
            self.state.ear_side = "right"

    def _on_use_scan_clicked(self):
        volume_node = self.ui.volumeSelector.currentNode()
        if volume_node is None:
            self.ui.statusLabel.setText("Please select a loaded scan first.")
            return

        self.state.volume_node = volume_node
        self.ui.statusLabel.setText(f"Using: {volume_node.GetName()}")

    def on_leave_next(self):
        if self.state.volume_node is None:
            return False, "Please select and confirm a scan before continuing."
        if self.state.ear_side not in ("left", "right"):
            return False, "Please specify whether this case is for the left or right ear."
        return True, ""
