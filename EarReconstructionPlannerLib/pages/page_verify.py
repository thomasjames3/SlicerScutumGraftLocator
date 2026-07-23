"""
page_verify.py
================
Page 8: final checkpoint. Shows both isolated meshes (already loaded as
model nodes in Slicer's own 3D view) and requires the surgeon to
explicitly confirm each looks correct before the curvature comparison
runs. This is a deliberate manual gate, not just a formality -- it's the
last point where a bad segmentation or a sloppy drawn outline gets caught
before it feeds into the surgical planning output.

Expected widgets in page_verify.ui:
  - scutumApprovedCheckBox  (QCheckBox)
  - pinnaApprovedCheckBox   (QCheckBox)
  - statusLabel             (QLabel)
"""

from __future__ import annotations
from EarReconstructionPlannerLib.pages.base_page import WizardPage


class VerifyPage(WizardPage):
    def on_enter(self):
        self.ui.scutumApprovedCheckBox.setChecked(self.state.surgeon_approved_scutum)
        self.ui.pinnaApprovedCheckBox.setChecked(self.state.surgeon_approved_pinna)
        self.ui.scutumApprovedCheckBox.toggled.connect(self._on_scutum_toggled)
        self.ui.pinnaApprovedCheckBox.toggled.connect(self._on_pinna_toggled)
        self.ui.statusLabel.setText(
            "Review both meshes in the 3D view. Check each box once you're "
            "satisfied it's accurate. Go back to any earlier step to make "
            "corrections if needed."
        )

    def _on_scutum_toggled(self, checked):
        self.state.surgeon_approved_scutum = checked

    def _on_pinna_toggled(self, checked):
        self.state.surgeon_approved_pinna = checked

    def on_leave_next(self):
        if not self.state.surgeon_approved_scutum:
            return False, "Please confirm the scutum defect mesh looks correct."
        if not self.state.surgeon_approved_pinna:
            return False, "Please confirm the pinna mesh looks correct."
        return True, ""
