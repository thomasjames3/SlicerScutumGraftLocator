"""
page_complete.py
====================
Page 10 (final): the wizard's actual completion action. Everything else is
done by the time the surgeon reaches here (both meshes approved, the
curvature comparison run) -- this page just lets them save whichever
result/intermediate files they want to a folder of their choice.

Moved here from page_curvature.py (which used to have its own "Download
results" section at the bottom) so the curvature page can stay focused on
running the comparison and reviewing the heatmap/candidates, with a
distinct, dedicated "you're done, here's what to keep" step after it --
and so every mesh generated anywhere in the wizard (not just the two
curvature inputs) has a download option in one place, not scattered across
whichever page happened to produce each one.

_DOWNLOADABLE_FILES drives both on_enter() (enabling/disabling each
checkbox based on whether that file actually exists yet) and
_on_download_clicked() (what to copy) from one list, so adding another
downloadable file later is a one-line change here rather than touching
both methods separately.

Expected widgets in page_complete.ui:
  - tutorialLabel                    (QLabel) -- extra guidance, shown only in tutorial mode
  - completionLabel                  (QLabel)
  - downloadHeatmapCheckBox          (QCheckBox)
  - downloadScutumDefectCheckBox     (QCheckBox)
  - downloadPinnaIsolatedCheckBox    (QCheckBox)
  - downloadScutumBoneWallCheckBox   (QCheckBox)
  - downloadRankedSitesCheckBox      (QCheckBox) -- unchecked by default; the raw
      ranked-candidates CSV behind the curvature page's results table, not
      something most surgeons need to keep
  - downloadDestinationLineEdit      (QLineEdit, read-only) -- chosen destination folder
  - browseDestinationButton          (QPushButton)
  - downloadButton                   (QPushButton)
  - downloadStatusLabel              (QLabel)
"""

from __future__ import annotations
import shutil
from EarReconstructionPlannerLib.pages.base_page import WizardPage

# (checkbox widget name, WizardState field name, message shown if checked
# but the file turns out to be missing -- shouldn't normally happen since
# the checkbox is disabled whenever the field is None, but on_leave_next
# for earlier pages doesn't hard-block revisiting this page after a
# "Revert to Here" cleared something downstream).
_DOWNLOADABLE_FILES = [
    ("downloadHeatmapCheckBox", "heatmap_output_path",
     "The pinna heatmap isn't available -- go back and run the curvature comparison first."),
    ("downloadScutumDefectCheckBox", "scutum_defect_mesh_path",
     "The scutum defect mesh isn't available -- go back and isolate it first."),
    ("downloadPinnaIsolatedCheckBox", "pinna_isolated_mesh_path",
     "The isolated pinna mesh isn't available -- go back and isolate it first."),
    ("downloadScutumBoneWallCheckBox", "scutum_bone_wall_mesh_path",
     "The scutum bone wall mesh isn't available -- go back and run scutum segmentation first."),
    ("downloadRankedSitesCheckBox", "ranked_sites_csv_path",
     "The ranked candidates CSV isn't available -- go back and run the curvature comparison first."),
]


class CompletePage(WizardPage):
    def __init__(self, ui, state):
        super().__init__(ui, state)
        self._download_destination_dir = None

    def on_enter(self):
        self.set_tutorial_text(
            "This is the last step -- nothing further to plan. Tick whichever "
            "files you want to save (the four meshes are checked by default; "
            "the ranked harvest site candidates CSV is unchecked by default "
            "since most surgeons won't need the raw table, tick it if you "
            "want it. A checkbox is grayed out if that particular file was "
            "never generated, e.g. if you skipped back and changed something "
            "since), click 'Browse...' to choose a destination folder, then "
            "'Download Selected Files' to copy them there."
        )
        self.ui.browseDestinationButton.clicked.connect(self._on_browse_destination_clicked)
        self.ui.downloadButton.clicked.connect(self._on_download_clicked)
        self.ui.downloadStatusLabel.setText("")

        for checkbox_name, field_name, _ in _DOWNLOADABLE_FILES:
            checkbox = getattr(self.ui, checkbox_name)
            available = getattr(self.state, field_name) is not None
            checkbox.setEnabled(available)
            if not available:
                checkbox.setChecked(False)

    def _on_browse_destination_clicked(self):
        import qt

        chosen = qt.QFileDialog.getExistingDirectory(
            None, "Choose Download Destination", self._download_destination_dir or ""
        )
        if chosen:
            self._download_destination_dir = chosen
            self.ui.downloadDestinationLineEdit.setText(chosen)

    def _on_download_clicked(self):
        if not self._download_destination_dir:
            self.ui.downloadStatusLabel.setText("Choose a download destination folder first.")
            return

        to_copy = []
        for checkbox_name, field_name, missing_message in _DOWNLOADABLE_FILES:
            if not getattr(self.ui, checkbox_name).isChecked():
                continue
            path = getattr(self.state, field_name)
            if path is None:
                self.ui.downloadStatusLabel.setText(missing_message)
                return
            to_copy.append(path)

        if not to_copy:
            self.ui.downloadStatusLabel.setText("Select at least one file to download.")
            return

        for src_path in to_copy:
            shutil.copy2(src_path, self._download_destination_dir)

        self.ui.downloadStatusLabel.setText(
            f"Downloaded {len(to_copy)} file(s) to {self._download_destination_dir}."
        )

    def is_final_page(self) -> bool:
        return True
