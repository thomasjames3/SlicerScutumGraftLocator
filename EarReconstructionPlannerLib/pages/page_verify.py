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
  - tutorialLabel           (QLabel) -- extra guidance, shown only in tutorial mode
  - scutumApprovedCheckBox  (QCheckBox)
  - pinnaApprovedCheckBox   (QCheckBox)
  - statusLabel             (QLabel)
"""

from __future__ import annotations
from EarReconstructionPlannerLib.pages.base_page import WizardPage


class VerifyPage(WizardPage):
    def on_enter(self):
        self.set_tutorial_text(
            "Both meshes from the earlier steps are loaded in the 3D view "
            "now -- use left-drag to rotate, scroll or right-drag to zoom, "
            "and middle-drag to pan to inspect each one from every angle. "
            "Look for anything that looks obviously wrong: missing chunks, "
            "extra unrelated material, or a shape that doesn't match what "
            "you'd expect.\n\n"
            "If something looks off, use the Back button (or jump further "
            "back using a page's 'Revert to Here' option) to redo that "
            "step -- there's no penalty for going back and adjusting a "
            "threshold or redrawing an outline. Only check a box once "
            "you're confident that mesh is accurate; the final comparison "
            "on the next page is only as good as these two inputs."
        )
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
