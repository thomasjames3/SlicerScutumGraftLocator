"""
page_curvature.py
====================
Page 9 (final): run the curvature comparison against the two approved
meshes, load the resulting heatmap mesh into the 3D view, and let the
surgeon download whichever result files they want.

See curvature_integration.py for how this actually runs the comparison
(in-process, via core/curvature/pipeline.py -- no separate Python install,
venv, or subprocess; no Slicer/Qt dependency in that module at all). This
page is the Qt-facing half: it streams the comparison's progress into
progressTextEdit as it runs (so a run that takes a couple of minutes on a
dense mesh doesn't look like Slicer has frozen), then loads the heatmap
model and shows the ranked harvest-site candidates in a table once it's
done.

This is the wizard's final page, so instead of the generic shared
"Finish" button (which used to just validate that a run had completed and
then do nothing further -- there's no next page to advance to), the main
module hides that shared button entirely here (see is_final_page() usage
in EarReconstructionPlanner.py's _show_page()) in favor of the download
section below, which is this page's actual completion action.

Expected widgets in page_curvature.ui:
  - runButton                    (QPushButton)
  - statusLabel                  (QLabel)
  - progressTextEdit             (QPlainTextEdit, read-only) -- live comparison progress
  - resultsTableWidget           (QTableWidget) -- ranked harvest site candidates
  - openOutputFolderButton       (QPushButton) -- opens the scratch output/ folder
  - downloadHeatmapCheckBox      (QCheckBox)
  - downloadScutumDefectCheckBox (QCheckBox)
  - downloadDestinationLineEdit  (QLineEdit, read-only) -- chosen destination folder
  - browseDestinationButton      (QPushButton)
  - downloadButton               (QPushButton)
  - downloadStatusLabel          (QLabel)
"""

from __future__ import annotations
import logging
import os
import shutil
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from EarReconstructionPlannerLib import curvature_integration

logger = logging.getLogger(__name__)

_TABLE_HEADERS = ["Rank", "Coarse Score", "Chamfer (mm)", "Hausdorff (mm)", "X", "Y", "Z"]


class CurvaturePage(WizardPage):
    def __init__(self, ui, state):
        super().__init__(ui, state)
        # Reentrancy guard: on_enter() reconnects runButton.clicked every
        # time this page is shown (same convention every other page in
        # this wizard follows), which means a duplicate-connected click
        # would otherwise fire _on_run_clicked multiple times for one
        # click. That's harmless on the threshold/review pages (their work
        # is synchronous and idempotent-ish), but here it would launch
        # multiple concurrent comparison runs sharing the same scratch
        # output folder and stomping on each other's files. This flag
        # makes every call after the first a no-op while a run is in
        # progress, regardless of how many times the signal is connected.
        self._running = False
        self._download_destination_dir = None

    def on_enter(self):
        self.ui.runButton.clicked.connect(self._on_run_clicked)
        self.ui.openOutputFolderButton.clicked.connect(self._on_open_output_folder_clicked)
        self.ui.browseDestinationButton.clicked.connect(self._on_browse_destination_clicked)
        self.ui.downloadButton.clicked.connect(self._on_download_clicked)

        self.ui.resultsTableWidget.setColumnCount(len(_TABLE_HEADERS))
        self.ui.resultsTableWidget.setHorizontalHeaderLabels(_TABLE_HEADERS)

        if not curvature_integration.is_configured():
            self.ui.statusLabel.setText(curvature_integration.describe_configuration_problem())
            self.ui.runButton.setEnabled(False)
        elif self.state.heatmap_output_path:
            self.ui.statusLabel.setText(
                "Already generated. Click Run again to redo the comparison, "
                "e.g. after going back and correcting a mesh."
            )
        else:
            self.ui.statusLabel.setText("Ready. Click Run to generate the heatmap.")

    def _output_dir(self):
        import slicer
        return os.path.join(self.state.working_dir or slicer.app.temporaryPath, "heatmap_output")

    def _on_progress_line(self, line: str):
        import slicer
        if line:
            self.ui.progressTextEdit.appendPlainText(line)
        # Pump the Qt event loop on every callback tick (including the
        # empty-string heartbeat ticks curvature_integration sends during
        # silent stretches) so the UI stays responsive and visibly alive
        # for the whole run, instead of looking frozen until it finishes.
        slicer.app.processEvents()

    def _on_run_clicked(self):
        if self._running:
            return
        self._running = True

        import slicer

        self.ui.runButton.setEnabled(False)
        self.ui.progressTextEdit.clear()
        self.ui.resultsTableWidget.setRowCount(0)
        self.ui.statusLabel.setText("Running curvature comparison...")
        slicer.app.processEvents()

        output_dir = self._output_dir()

        try:
            heatmap_path = curvature_integration.run_curvature_comparison(
                self.state.pinna_isolated_mesh_path,
                self.state.scutum_defect_mesh_path,
                output_dir,
                progress_callback=self._on_progress_line,
            )
        except Exception as e:
            self.ui.statusLabel.setText(f"Error: {e}")
            self.ui.runButton.setEnabled(True)
            self._running = False
            return

        self.state.heatmap_output_path = heatmap_path

        if self.state.heatmap_model_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.heatmap_model_node)
        self.state.heatmap_model_node = slicer.util.loadModel(heatmap_path)
        self._enable_vertex_color_display(self.state.heatmap_model_node)

        threeDView = slicer.app.layoutManager().threeDWidget(0).threeDView()
        threeDView.resetFocalPoint()
        threeDView.resetCamera()

        csv_path = curvature_integration.get_ranked_candidates_csv_path(output_dir)
        self.state.ranked_sites_csv_path = csv_path
        rows = curvature_integration.read_ranked_candidates(csv_path)
        self._populate_results_table(rows)

        self.ui.openOutputFolderButton.setEnabled(os.path.isdir(os.path.join(output_dir, "output")))

        if rows:
            best = rows[0]
            self.ui.statusLabel.setText(
                f"Done. Best match: rank 1 (coarse score {_format_float(best['coarse_score'])}, "
                f"chamfer distance {_format_float(best['chamfer_distance'])} mm). "
                "Heatmap loaded in the 3D view -- green is a better match, red is worse."
            )
        else:
            self.ui.statusLabel.setText(
                "Done, but no ranked candidates were found in the results -- "
                "check the progress log above."
            )

        self.ui.runButton.setEnabled(True)
        self._running = False

    def _enable_vertex_color_display(self, model_node):
        """Curvature Project v4's heatmap is a mesh with per-vertex colors
        baked in (red/yellow/green similarity, highlighted top sites);
        vtkPLYReader reads those in as the model's active point-data
        scalars, but Slicer's model display node doesn't always turn
        scalar visibility on by itself. Explicitly enabling it here makes
        sure the colors actually show up rather than a flat gray mesh.
        Wrapped defensively and never raises -- this is cosmetic, hasn't
        been runtime-confirmed against a real Slicer install yet (no
        Slicer available in the dev environment), and shouldn't block the
        rest of the workflow if it doesn't work as expected."""
        try:
            display_node = model_node.GetDisplayNode()
            poly_data = model_node.GetPolyData()
            if display_node is None or poly_data is None:
                return
            scalars = poly_data.GetPointData().GetScalars()
            if scalars is None:
                return
            display_node.SetActiveScalarName(scalars.GetName())
            display_node.SetScalarVisibility(True)
        except Exception:
            logger.exception("Couldn't enable vertex-color display on the heatmap model")

    def _populate_results_table(self, rows):
        import qt

        table = self.ui.resultsTableWidget
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.get("rank", ""),
                _format_float(row.get("coarse_score", "")),
                _format_float(row.get("chamfer_distance", "")),
                _format_float(row.get("hausdorff_distance", "")),
                _format_float(row.get("x", "")),
                _format_float(row.get("y", "")),
                _format_float(row.get("z", "")),
            ]
            for col_index, value in enumerate(values):
                table.setItem(row_index, col_index, qt.QTableWidgetItem(str(value)))
        table.resizeColumnsToContents()

    def _on_open_output_folder_clicked(self):
        import qt

        folder = os.path.join(self._output_dir(), "output")
        if os.path.isdir(folder):
            qt.QDesktopServices.openUrl(qt.QUrl.fromLocalFile(folder))

    def _on_browse_destination_clicked(self):
        import qt

        chosen = qt.QFileDialog.getExistingDirectory(
            None, "Choose Download Destination", self._download_destination_dir or ""
        )
        if chosen:
            self._download_destination_dir = chosen
            self.ui.downloadDestinationLineEdit.setText(chosen)

    def _on_download_clicked(self):
        want_heatmap = self.ui.downloadHeatmapCheckBox.isChecked()
        want_scutum_defect = self.ui.downloadScutumDefectCheckBox.isChecked()

        if not want_heatmap and not want_scutum_defect:
            self.ui.downloadStatusLabel.setText("Select at least one file to download.")
            return
        if not self._download_destination_dir:
            self.ui.downloadStatusLabel.setText("Choose a download destination folder first.")
            return

        to_copy = []
        if want_heatmap:
            if self.state.heatmap_output_path is None:
                self.ui.downloadStatusLabel.setText(
                    "Run the curvature comparison before downloading the heatmap."
                )
                return
            to_copy.append(self.state.heatmap_output_path)
        if want_scutum_defect:
            if self.state.scutum_defect_mesh_path is None:
                self.ui.downloadStatusLabel.setText(
                    "The scutum defect mesh isn't available -- go back and isolate it first."
                )
                return
            to_copy.append(self.state.scutum_defect_mesh_path)

        for src_path in to_copy:
            shutil.copy2(src_path, self._download_destination_dir)

        self.ui.downloadStatusLabel.setText(
            f"Downloaded {len(to_copy)} file(s) to {self._download_destination_dir}."
        )

    def is_final_page(self) -> bool:
        return True


def _format_float(value_str, decimals=3) -> str:
    try:
        value = float(value_str)
    except (TypeError, ValueError):
        return str(value_str)
    if value == float("inf"):
        return "N/A"
    return f"{value:.{decimals}f}"
