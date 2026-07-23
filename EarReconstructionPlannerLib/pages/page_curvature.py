"""
page_curvature.py
====================
Page 9 (final): run Curvature Project v4 against the two approved meshes
and load the resulting heatmap mesh into the 3D view.

See curvature_integration.py for the current state of this connection --
as of this scaffold, it's a placeholder waiting on the actual Curvature
Project v4 source. This page will show a clear "not set up yet" message
via is_configured() until that's wired up, rather than crashing.

Expected widgets in page_curvature.ui:
  - runButton      (QPushButton)
  - statusLabel     (QLabel)
"""

from __future__ import annotations
import os
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from EarReconstructionPlannerLib import curvature_integration


class CurvaturePage(WizardPage):
    def on_enter(self):
        self.ui.runButton.clicked.connect(self._on_run_clicked)
        if curvature_integration.is_configured():
            self.ui.statusLabel.setText("Ready. Click Run to generate the heatmap.")
        else:
            self.ui.statusLabel.setText(
                "Curvature Project v4 isn't connected yet -- this step will be "
                "enabled once that integration is finished."
            )
            self.ui.runButton.setEnabled(False)

    def _on_run_clicked(self):
        import slicer

        self.ui.statusLabel.setText("Running curvature comparison...")
        slicer.app.processEvents()

        output_dir = os.path.join(self.state.working_dir or slicer.app.temporaryPath, "heatmap_output")

        try:
            heatmap_path = curvature_integration.run_curvature_comparison(
                self.state.pinna_isolated_mesh_path,
                self.state.scutum_defect_mesh_path,
                output_dir,
            )
        except Exception as e:
            self.ui.statusLabel.setText(f"Error: {e}")
            return

        self.state.heatmap_output_path = heatmap_path
        slicer.util.loadModel(heatmap_path)

        csv_path = curvature_integration.get_ranked_candidates_csv_path(output_dir)
        csv_note = f"\nRanked harvest sites: {csv_path}" if os.path.isfile(csv_path) else ""
        self.ui.statusLabel.setText(f"Done. Heatmap loaded from: {heatmap_path}{csv_note}")

    def is_final_page(self) -> bool:
        return True
