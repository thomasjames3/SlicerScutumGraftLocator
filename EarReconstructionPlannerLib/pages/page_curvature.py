"""
page_curvature.py
====================
Page 9: run the curvature comparison against the two approved meshes,
load the resulting heatmap mesh into the 3D view, and show the ranked
harvest-site candidates. Downloading result files is the *next* page's
job (page_complete.py) -- this page used to have its own "Download
results" section, but that's been split out into a dedicated final
"Complete" page so every mesh the wizard produces (not just the two
curvature inputs) has one common download step, instead of scattering a
download section on whichever page happened to produce each file.

See curvature_integration.py for how this actually runs the comparison
(in-process, via core/curvature/pipeline.py -- no separate Python install,
venv, or subprocess; no Slicer/Qt dependency in that module at all). This
page is the Qt-facing half: it streams the comparison's progress into
statusLabel as it runs (so a run that takes a couple of minutes on a
dense mesh doesn't look like Slicer has frozen -- an earlier version used
a separate scrolling log box for this, but that read like a raw Python
terminal, which isn't something a surgeon needs to see), then loads the
heatmap model and shows the ranked harvest-site candidates in a table
once it's done.

Each results-table row has a "Locate" button that drops a labeled Markups
point ("1", "2", ...) on the heatmap mesh at that candidate's exact
vertex, so the surgeon can find it in the 3D view without having to
eyeball X/Y/Z coordinates -- click again to hide it. All candidate points
live in one `state.harvest_site_markup_node` (one control point per row,
index-aligned with the table), created hidden; only per-point visibility
toggles, mirroring the button clicks -- see _populate_results_table()/
_on_locate_toggled().

Not the wizard's final page anymore (see page_complete.py) -- "Next"
here behaves like every other page's, advancing to the Complete page's
download step.

Expected widgets in page_curvature.ui:
  - tutorialLabel                (QLabel) -- extra guidance, shown only in tutorial mode
  - runButton                    (QPushButton)
  - progressBar                  (QProgressBar) -- see _on_progress_line() below; shown only
    during a run, determinate (N of M) during the candidate-scoring/refinement loops,
    indeterminate ("busy") during named steps with no known fraction
  - statusLabel                  (QLabel) -- also shows live progress while a run is in flight
  - resultsTableWidget           (QTableWidget) -- ranked harvest site candidates, each row has a Locate button in the last column
  - openOutputFolderButton       (QPushButton) -- opens the scratch output/ folder
"""

from __future__ import annotations
import logging
import os
import re
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from EarReconstructionPlannerLib import curvature_integration
import config

logger = logging.getLogger(__name__)

_TABLE_HEADERS = ["Rank", "Coarse Score", "Chamfer (mm)", "Hausdorff (mm)", "X", "Y", "Z", "Locate"]
_LOCATE_COLUMN = len(_TABLE_HEADERS) - 1

# core/curvature/pipeline.py's progress() calls are plain human-readable
# strings (that module has no Slicer/Qt dependency at all, deliberately --
# see its own docstring), so this page recovers real N-of-M progress by
# pattern-matching the specific known message formats it emits, rather
# than changing that module's interface. Two phases report a running
# count this way: candidate scoring ("Scoring 300 candidates..." then
# periodic "  scored 30/300 candidates" lines) and top-candidate
# refinement ("Refining top 15 candidates..." then "  [3/15] vertex ...").
# Any other line (e.g. "Loading meshes...", "Done.") doesn't match either
# pattern, so _on_progress_line() falls back to an indeterminate/"busy"
# bar for those -- still visibly animating, just without a known fraction.
_PHASE_TOTAL_RE = re.compile(r"^(?:Scoring|Refining top) (\d+)")
_PHASE_PROGRESS_RE = re.compile(r"scored (\d+)/(\d+) candidates|^\s*\[(\d+)/(\d+)\]")


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

    def on_enter(self):
        self.set_tutorial_text(
            "This step compares the two meshes you approved and scores "
            "every spot on the pinna as a possible cartilage graft harvest "
            "site. Click 'Run Curvature Comparison' -- it can take anywhere "
            "from a few seconds to a couple of minutes, and the status line "
            "above will keep updating so you know it's still working.\n\n"
            "When it finishes, the pinna re-loads in the 3D view colored "
            "like a heatmap: green means a better match to the defect's "
            "shape, red means a worse one (use left-drag/scroll/middle-drag "
            "as usual to rotate/zoom/pan and inspect it from different "
            "angles). The blue-highlighted patches are the 3 best harvest "
            "sites, shaped to match the actual defect's footprint (not just "
            "a circle) -- these are worth looking at first. The table below "
            "lists the top-ranked candidate sites with their scores and 3D "
            "coordinates, best first -- click a row's 'Show Point' button to "
            "drop a labeled marker on the mesh at that exact spot, and click "
            "it again ('Hide Point') to remove it.\n\n"
            "What 'Chamfer' and 'Hausdorff' mean: both come from actually "
            "fitting the candidate patch against the defect's shape (after "
            "finding the best alignment between them), not just the coarse "
            "similarity score. 'Chamfer (mm)' is the AVERAGE mismatch "
            "across the whole patch once aligned -- lower means the site's "
            "overall shape more closely follows the defect's contour, so "
            "this is the main number to rank sites by. 'Hausdorff (mm)' is "
            "the single WORST mismatch anywhere on the patch -- a low "
            "Chamfer paired with a high Hausdorff usually means the site "
            "fits well on average but has one problem spot (e.g. a corner "
            "that pokes out or falls short), which is worth checking "
            "directly in the 3D view before settling on that site over a "
            "close second choice.\n\n"
            "Click Next when you're ready to download the heatmap and/or "
            "any of the meshes generated along the way."
        )
        self.ui.runButton.clicked.connect(self._on_run_clicked)
        self.ui.openOutputFolderButton.clicked.connect(self._on_open_output_folder_clicked)
        self.ui.progressBar.setVisible(False)

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
            self.ui.statusLabel.setText(line)

            total_match = _PHASE_TOTAL_RE.match(line)
            progress_match = _PHASE_PROGRESS_RE.search(line)
            if total_match:
                # A new phase just started ("Scoring 300 candidates..."/
                # "Refining top 15 candidates...") -- (re)set the bar to a
                # fresh determinate 0..total range for this phase.
                total = int(total_match.group(1))
                self.ui.progressBar.setMinimum(0)
                self.ui.progressBar.setMaximum(total)
                self.ui.progressBar.setValue(0)
            elif progress_match:
                # One of the two "N/M" progress lines mid-phase -- groups
                # 1/2 are the scoring-loop's pair, 3/4 the refine-loop's
                # (whichever pair the regex actually matched has real
                # ints, the other pair is None).
                current = int(progress_match.group(1) or progress_match.group(3))
                total = int(progress_match.group(2) or progress_match.group(4))
                self.ui.progressBar.setMinimum(0)
                self.ui.progressBar.setMaximum(total)
                self.ui.progressBar.setValue(current)
            else:
                # A named step with no known fraction (e.g. "Loading
                # meshes...", "Computing curvature descriptors...",
                # "Done.") -- switch to Qt's indeterminate/"busy" idiom
                # (min=max=0) so the bar still visibly animates instead of
                # sitting frozen at wherever the last phase left it.
                self.ui.progressBar.setMinimum(0)
                self.ui.progressBar.setMaximum(0)
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
        self.ui.resultsTableWidget.setRowCount(0)
        self.ui.statusLabel.setText("Running curvature comparison...")
        # Indeterminate/"busy" to start -- _on_progress_line() switches this
        # to a determinate N-of-M bar once the run reaches a phase that
        # reports a real count (candidate scoring/refinement).
        self.ui.progressBar.setVisible(True)
        self.ui.progressBar.setMinimum(0)
        self.ui.progressBar.setMaximum(0)
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
            self.ui.progressBar.setVisible(False)
            self._running = False
            return
        self.ui.progressBar.setVisible(False)

        self.state.heatmap_output_path = heatmap_path

        if self.state.heatmap_model_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.heatmap_model_node)
        self.state.heatmap_model_node = slicer.util.loadModel(heatmap_path)
        self._enable_vertex_color_display(self.state.heatmap_model_node)

        # Recenter the 3D view on the new heatmap and orient the camera to
        # look from the correct side for this ear -- same as the scutum/
        # pinna review pages (see base_page.WizardPage.recenter_3d_view()).
        self.recenter_3d_view()

        # Fresh markup node for this run's Locate points, same
        # remove-then-recreate convention as heatmap_model_node above --
        # otherwise a second run would leave the first run's points
        # orphaned in the scene alongside the new ones.
        if self.state.harvest_site_markup_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.harvest_site_markup_node)
        harvest_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLMarkupsFiducialNode", "HarvestSiteCandidates"
        )
        harvest_node.SetLocked(True)
        harvest_node.CreateDefaultDisplayNodes()
        self.set_absolute_point_size(harvest_node, config.HARVEST_SITE_POINT_SIZE_MM)
        display_node = harvest_node.GetDisplayNode()
        if display_node is not None:
            # Magenta stands out against the heatmap's red/yellow/green
            # scoring colors and its blue top-site fill/black outline.
            display_node.SetColor(1.0, 0.0, 1.0)
            display_node.SetSelectedColor(1.0, 0.0, 1.0)
        self.state.harvest_site_markup_node = harvest_node

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
                "Heatmap loaded in the 3D view -- green is a better match, red is worse. "
                "Use each row's Locate button to mark a candidate on the mesh."
            )
        else:
            self.ui.statusLabel.setText(
                "Done, but no ranked candidates were found in the results."
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
        """Fills the table AND, in the same pass, adds one hidden Markups
        control point per row to state.harvest_site_markup_node (row index
        == control point index, kept 1:1 so _on_locate_toggled() can find
        a row's point by row_index alone). Points start hidden so the
        heatmap isn't cluttered with all of them at once -- the surgeon
        opts in per candidate via each row's Locate button.

        The point position is read directly from the already-loaded
        heatmap model's polydata (GetPoint(vertex_id)) rather than
        re-parsing the CSV's x/y/z strings -- same underlying numbers
        (both trace back to the same pinna mesh vertex array), but this
        guarantees the point lands exactly on the rendered mesh's own
        vertex rather than depending on a text round-trip.
        """
        import qt
        import vtk

        table = self.ui.resultsTableWidget
        table.setRowCount(len(rows))

        harvest_node = self.state.harvest_site_markup_node
        poly_data = self.state.heatmap_model_node.GetPolyData() if self.state.heatmap_model_node else None

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

            if harvest_node is not None and poly_data is not None and row.get("vertex_id", "") != "":
                point = poly_data.GetPoint(int(row["vertex_id"]))
                rank_label = str(row.get("rank", row_index + 1))
                harvest_node.AddControlPointWorld(vtk.vtkVector3d(*point), rank_label)
                harvest_node.SetNthControlPointVisibility(row_index, False)

            locate_button = qt.QPushButton("Show Point")
            locate_button.clicked.connect(lambda checked=False, i=row_index: self._on_locate_toggled(i))
            table.setCellWidget(row_index, _LOCATE_COLUMN, locate_button)

        table.resizeColumnsToContents()

    def _on_locate_toggled(self, row_index):
        """Show/hide harvest_site_markup_node's row_index'th control point
        (the button's text is kept in sync as the single source of truth
        for what "toggled" means, rather than tracking a separate bool)."""
        harvest_node = self.state.harvest_site_markup_node
        button = self.ui.resultsTableWidget.cellWidget(row_index, _LOCATE_COLUMN)
        if harvest_node is None or row_index >= harvest_node.GetNumberOfControlPoints():
            return
        now_visible = not harvest_node.GetNthControlPointVisibility(row_index)
        harvest_node.SetNthControlPointVisibility(row_index, now_visible)
        if button is not None:
            button.setText("Hide Point" if now_visible else "Show Point")

    def _on_open_output_folder_clicked(self):
        import qt

        folder = os.path.join(self._output_dir(), "output")
        if os.path.isdir(folder):
            qt.QDesktopServices.openUrl(qt.QUrl.fromLocalFile(folder))


def _format_float(value_str, decimals=3) -> str:
    try:
        value = float(value_str)
    except (TypeError, ValueError):
        return str(value_str)
    if value == float("inf"):
        return "N/A"
    return f"{value:.{decimals}f}"
