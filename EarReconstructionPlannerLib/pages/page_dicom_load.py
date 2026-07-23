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
  - volumeSelector       (qMRMLNodeComboBox, filtered to vtkMRMLScalarVolumeNode)
  - resampleButton        (QPushButton) -- "Use this scan"
  - statusLabel          (QLabel)
"""

from __future__ import annotations
from EarReconstructionPlannerLib.pages.base_page import WizardPage


class DicomLoadPage(WizardPage):
    def on_enter(self):
        import slicer
        self.ui.volumeSelector.setMRMLScene(slicer.mrmlScene)
        self.ui.resampleButton.clicked.connect(self._on_use_scan_clicked)

        if not slicer.mrmlScene.GetNodesByClass("vtkMRMLScalarVolumeNode").GetNumberOfItems():
            self.ui.statusLabel.setText(
                "No volumes loaded yet. Use Slicer's DICOM module (or File > "
                "Add Data) to load a scan first, then come back to this page "
                "and select it below."
            )
        else:
            self.ui.statusLabel.setText("")

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
        return True, ""
