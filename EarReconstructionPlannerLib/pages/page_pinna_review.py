"""
page_pinna_review.py
=======================
Page 4: run the pinna region (skin surface) segmentation and let the
surgeon review/adjust the threshold before drawing the pinna outline on
the next page.

Mirrors page_scutum_review.py's structure -- see that file's docstring for
the general pattern. The key difference: this segments the outer skin
surface within a region around the ear (core/segment_pinna_threshold.py),
not a bone wall -- the actual "isolate just the pinna" step happens on the
next page (page_pinna_draw.py), same two-step split as the scutum stage.

Expected widgets in page_pinna_review.ui:
  - tutorialLabel             (QLabel) -- extra guidance, shown only in tutorial mode
  - skinThresholdSlider       (QSlider or ctkSliderWidget)
  - runButton                 (QPushButton)
  - openSegmentEditorButton  (QPushButton)
  - resetPageButton           (QPushButton) -- clear this page's own segmentation
  - revertToHereButton        (QPushButton) -- clear every later step, keep this segmentation
  - progressBar               (QProgressBar) -- shown only while a background step (see
    base_page.WizardPage.run_blocking()) is running, hidden otherwise
  - statusLabel               (QLabel)
"""

from __future__ import annotations
import os
import time
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from EarReconstructionPlannerLib import wizard_state
from core import roi_crop, segment_pinna_threshold, postprocess, mesh_export, io_utils
import config


class PinnaReviewPage(WizardPage):
    def on_enter(self):
        # Re-show this stage's own model in case it was hidden by the
        # scutum review page (see page_scutum_review.py's on_enter) after
        # the surgeon navigated forward into the scutum stage and then
        # back again -- but only if it hasn't since been superseded by an
        # isolated patch, which should stay hidden (see the "original
        # models hidden once isolated successor exists" feature).
        if self.state.pinna_region_model_node is not None:
            self.state.pinna_region_model_node.GetDisplayNode().SetVisibility(
                self.state.pinna_isolated_model_node is None
            )

        self.set_tutorial_text(
            "This step finds the outer skin surface around the ear you "
            "marked (not the cartilage itself -- that gets isolated by "
            "hand-drawing on the next page). The 'Skin threshold' slider "
            "controls how bright a voxel must be to count as skin/tissue "
            "versus air.\n\n"
            "Click 'Run Segmentation' with the default first, then check "
            "the result in the 3D view (left-drag rotates, scroll or "
            "right-drag zooms, middle-drag pans). If it looks wrong -- too "
            "much missing, or too much extra material included -- drag the "
            "slider and click Run again; you can repeat this as many times "
            "as needed. For small manual fixes, 'Open Segment Editor for "
            "Manual Touch-Up' opens Slicer's own paint/erase tool (pick "
            "'Paint' or 'Erase' on the left, adjust brush size, then click "
            "or drag in a slice view) -- return to this module afterwards."
        )
        self.ui.skinThresholdSlider.minimum = config.SKIN_THRESHOLD_ADJUST_RANGE[0]
        self.ui.skinThresholdSlider.maximum = config.SKIN_THRESHOLD_ADJUST_RANGE[1]
        self.ui.skinThresholdSlider.value = config.SKIN_AIR_THRESHOLD

        self.ui.runButton.clicked.connect(self._on_run_clicked)
        self.ui.openSegmentEditorButton.clicked.connect(self._on_open_segment_editor_clicked)
        self.ui.resetPageButton.clicked.connect(self._on_reset_page_clicked)
        self.ui.revertToHereButton.clicked.connect(self._on_revert_to_here_clicked)
        self.ui.openSegmentEditorButton.setEnabled(self.state.pinna_region_mesh_path is not None)
        self.ui.progressBar.setVisible(False)
        self.ui.statusLabel.setText("Adjust the slider if needed, then click Run.")

    def _on_run_clicked(self):
        import sitkUtils
        import slicer

        if self.state.volume_node is None:
            self.ui.statusLabel.setText("No scan loaded -- go back and select one first.")
            return

        # Re-running invalidates anything built on top of the old
        # segmentation (the pinna outline drawn on it, the verify
        # approval, the heatmap) -- see the matching comment in
        # page_scutum_review._on_run_clicked for why this has to happen
        # unconditionally, before the new segmentation is built.
        wizard_state.clear_downstream_state(self.state, "pinna_review")

        # Steps below run on a background thread via self.run_blocking() --
        # see that method's docstring in base_page.py. In short: several of
        # these single calls (segment_pinna_region, postprocess, especially
        # label_map_to_mesh's marching_cubes) run 5-20+ seconds each on a
        # real scan, and calling them directly on this (main) thread blocks
        # Slicer's event loop for that whole duration -- long enough that
        # Windows flags the app "Not Responding", which Thomas reported
        # surgeons could easily mistake for an actual crash. Running each
        # step on a background thread while this thread keeps pumping
        # slicer.app.processEvents() keeps the window visibly alive and
        # responsive throughout, and the progress bar below gives real
        # (if coarse -- one tick per named pipeline stage, not fine-grained
        # within a stage) confirmation that work is still happening. This
        # does not make the underlying computation any faster.
        self.ui.runButton.setEnabled(False)
        self.ui.progressBar.setVisible(True)
        self.ui.progressBar.setMinimum(0)
        self.ui.progressBar.setMaximum(7)
        self.ui.progressBar.setValue(0)
        try:
            self._run_segmentation_pipeline(sitkUtils, slicer)
        finally:
            self.ui.runButton.setEnabled(True)
            self.ui.progressBar.setVisible(False)

    def _run_segmentation_pipeline(self, sitkUtils, slicer):
        self.ui.statusLabel.setText("Segmenting skin surface near the ear...")
        slicer.app.processEvents()

        # See page_scutum_review.py / io_utils.flip_ras_lps for why this
        # conversion is required: PullVolumeFromSlicer() returns the image
        # in plain ITK/LPS convention, but pinna_landmarks.ear_center was
        # captured in Slicer's own RAS convention.
        sitk_image = io_utils.flip_ras_lps(sitkUtils.PullVolumeFromSlicer(self.state.volume_node))

        # TEMPORARY DIAGNOSTICS (see Known Issues #7 in CLAUDE.md) -- prints
        # to the Slicer Python console (View > Python console). Confirms
        # whether ear_center actually lands inside this scan's physical
        # (RAS mm) bounds -- if not, the ROI will be empty regardless of
        # threshold, and the fix is in landmark placement/capture, not
        # SKIN_AIR_THRESHOLD. Remove once #7 is resolved.
        import numpy as _np
        _origin = _np.array(sitk_image.GetOrigin())
        _size = _np.array(sitk_image.GetSize())
        _spacing = _np.array(sitk_image.GetSpacing())
        _direction = _np.array(sitk_image.GetDirection()).reshape(3, 3)
        _corner_far = _origin + (_size * _spacing) @ _direction.T
        print(f"[pinna diag] ear_center landmark (RAS mm): {self.state.pinna_landmarks.ear_center}")
        print(f"[pinna diag] volume physical corners (RAS mm): {tuple(_origin)} to {tuple(_corner_far)}")
        print(f"[pinna diag] volume size/spacing: {tuple(_size)} / {tuple(_spacing)}")

        # TEMPORARY TIMING INSTRUMENTATION (2026-07-29, see CLAUDE.md
        # "Pinna segmentation performance") -- the roi_crop grid-building
        # fix helped but Stage A is still slow on fine-native-spacing
        # scans. These prints (Slicer Python console) give a full
        # stage-by-stage breakdown so the next slowness report can be
        # diagnosed from real numbers instead of another guess. Remove
        # once the remaining bottleneck is found and addressed.
        _t_total = time.time()

        # Same coarse pre-crop fix as the scutum review page -- see
        # roi_crop.crop_to_point_region's docstring for why this has to
        # happen before build_spherical_roi_mask() on a real, full-
        # resolution scan.
        _t0 = time.time()
        coarse_cropped = roi_crop.crop_to_point_region(
            sitk_image,
            self.state.pinna_landmarks.ear_center,
            radius_mm=config.PINNA_ROI_RADIUS_MM,
        )
        print(f"[pinna timing] coarse crop: {time.time() - _t0:.2f}s (size {coarse_cropped.GetSize()})")

        _t0 = time.time()
        roi_mask = self.run_blocking(
            lambda: roi_crop.build_spherical_roi_mask(
                coarse_cropped,
                self.state.pinna_landmarks.ear_center,
                radius_mm=config.PINNA_ROI_RADIUS_MM,
            ),
            status_text="Building region of interest...",
        )
        self.ui.progressBar.setValue(1)
        print(f"[pinna timing] build_spherical_roi_mask: {time.time() - _t0:.2f}s")

        _t0 = time.time()

        def _crop_to_roi_bbox():
            return (
                roi_crop.crop_to_roi_bounding_box(coarse_cropped, roi_mask),
                roi_crop.crop_to_roi_bounding_box(roi_mask, roi_mask),
            )

        cropped_image, cropped_roi_mask = self.run_blocking(
            _crop_to_roi_bbox, status_text="Cropping to region of interest..."
        )
        self.ui.progressBar.setValue(2)
        print(f"[pinna timing] crop to ROI bounding box: {time.time() - _t0:.2f}s (size {cropped_image.GetSize()})")

        _t0 = time.time()
        region_mask = self.run_blocking(
            lambda: segment_pinna_threshold.segment_pinna_region(
                cropped_image,
                cropped_roi_mask,
                self.state.pinna_landmarks,
                threshold=self.ui.skinThresholdSlider.value,
            ),
            status_text="Segmenting skin surface...",
        )
        self.ui.progressBar.setValue(3)
        print(f"[pinna timing] segment_pinna_region TOTAL: {time.time() - _t0:.2f}s")

        _t0 = time.time()
        region_mask = self.run_blocking(
            lambda: postprocess.run_full_postprocess(region_mask),
            status_text="Cleaning up segmentation...",
        )
        self.ui.progressBar.setValue(4)
        print(f"[pinna timing] postprocess TOTAL: {time.time() - _t0:.2f}s")

        _t0 = time.time()
        region_mask = self.run_blocking(
            lambda: roi_crop.crop_to_own_bounding_box(region_mask, config.PINNA_TIGHT_CROP_MARGIN_MM),
            status_text="Cropping to tight bounding box...",
        )
        self.ui.progressBar.setValue(5)
        print(f"[pinna timing] crop_to_own_bounding_box: {time.time() - _t0:.2f}s (size {region_mask.GetSize()})")
        print(f"[pinna timing] Stage A grand total: {time.time() - _t_total:.2f}s")

        # Push into a segmentation node (not a plain labelmap) so it's
        # something Segment Editor can actually operate on -- see the
        # matching comment in page_scutum_review.py for why. The labelmap
        # node here is just a throwaway bridge for the conversion Slicer's
        # segmentations logic expects; it's removed once the segmentation
        # node owns the data.
        _t0 = time.time()
        temp_label_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "PinnaRegionTemp"
        )
        # region_mask is RAS-consistent; flip back to LPS since
        # PushVolumeToSlicer expects plain ITK convention (see
        # page_scutum_review.py for the matching comment).
        sitkUtils.PushVolumeToSlicer(io_utils.flip_ras_lps(region_mask), temp_label_node)
        print(f"[pinna timing] push labelmap to Slicer: {time.time() - _t0:.2f}s")

        if self.state.pinna_region_segmentation_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.pinna_region_segmentation_node)
        segmentation_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentationNode", "PinnaRegionSegmentation"
        )
        segmentation_node.CreateDefaultDisplayNodes()
        _t0 = time.time()
        slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
            temp_label_node, segmentation_node
        )
        print(f"[pinna timing] ImportLabelmapToSegmentationNode: {time.time() - _t0:.2f}s")
        slicer.mrmlScene.RemoveNode(temp_label_node)
        # Hidden by default -- see the matching comment in
        # page_scutum_review.py for why (avoids visually duplicating the
        # exported model node the surgeon actually draws on).
        segmentation_node.GetDisplayNode().SetVisibility(False)
        self.state.pinna_region_segmentation_node = segmentation_node
        # This freshly-built segmentation already went through
        # run_full_postprocess() once, right above -- see
        # _refresh_mesh_from_segmentation()'s matching comment for why
        # this flag exists.
        self._segmentation_edited = False

        # Uses the still-RAS-consistent `region_mask` so the exported
        # mesh's vertices line up correctly when loaded back into Slicer.
        _t0 = time.time()
        mesh = self.run_blocking(
            lambda: mesh_export.label_map_to_mesh(region_mask),
            status_text="Building 3D surface mesh...",
        )
        self.ui.progressBar.setValue(6)
        print(f"[pinna timing] label_map_to_mesh: {time.time() - _t0:.2f}s ({len(mesh.vertices)} verts, {len(mesh.faces)} faces)")

        _t0 = time.time()
        mesh = self.run_blocking(
            lambda: mesh_export.decimate_to_target_resolution(mesh),
            status_text="Simplifying mesh...",
        )
        self.ui.progressBar.setValue(7)
        print(f"[pinna timing] decimate_to_target_resolution: {time.time() - _t0:.2f}s ({len(mesh.vertices)} verts, {len(mesh.faces)} faces)")
        mesh_path = os.path.join(
            self.state.working_dir or slicer.app.temporaryPath, "pinna_region.stl"
        )
        _t0 = time.time()
        mesh_export.export_mesh(mesh, mesh_path)
        print(f"[pinna timing] export_mesh: {time.time() - _t0:.2f}s")
        self.state.pinna_region_mesh_path = mesh_path

        if self.state.pinna_region_model_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.pinna_region_model_node)
        _t0 = time.time()
        self.state.pinna_region_model_node = slicer.util.loadModel(mesh_path)
        print(f"[pinna timing] loadModel: {time.time() - _t0:.2f}s")

        # Hide the ear-center landmark now that the model exists -- same
        # reasoning as page_scutum_review.py.
        if self.state.pinna_landmarks_fiducial_node is not None:
            self.state.pinna_landmarks_fiducial_node.GetDisplayNode().SetVisibility(False)

        # Recenter and reorient the 3D view on the new model -- same
        # reasoning as page_scutum_review.py.
        self.recenter_3d_view()

        self.ui.openSegmentEditorButton.setEnabled(True)
        self.ui.statusLabel.setText(
            "Segmentation complete. Review it in the 3D view, adjust the "
            "slider and re-run if needed, or open Segment Editor for manual touch-ups."
        )

    def _on_open_segment_editor_clicked(self):
        import slicer

        # Mark the segmentation as possibly touched, so
        # _refresh_mesh_from_segmentation() knows to re-derive the mesh --
        # see that method's comment for why this matters.
        self._segmentation_edited = True

        slicer.util.selectModule("SegmentEditor")
        # Hide the drawing model and show the segmentation in its place --
        # otherwise the two would overlap in the 3D view while editing.
        if self.state.pinna_region_model_node is not None:
            self.state.pinna_region_model_node.GetDisplayNode().SetVisibility(False)
        segmentation_display_node = self.state.pinna_region_segmentation_node.GetDisplayNode()
        segmentation_display_node.SetVisibility(True)
        segmentation_display_node.SetVisibility3D(True)
        editor_widget = slicer.modules.segmenteditor.widgetRepresentation().self().editor
        editor_widget.setSegmentationNode(self.state.pinna_region_segmentation_node)
        # Slicer 5.2+ renamed "master volume" to "source volume" -- support
        # both since this hasn't been runtime-tested against a specific
        # Slicer version.
        if hasattr(editor_widget, "setSourceVolumeNode"):
            editor_widget.setSourceVolumeNode(self.state.volume_node)
        else:
            editor_widget.setMasterVolumeNode(self.state.volume_node)

    def _refresh_mesh_from_segmentation(self):
        """Re-bakes the drawn-on mesh from the segmentation node's current
        contents, to reflect manual Segment Editor edits. Only called (see
        on_leave_next()) if self._segmentation_edited is True -- i.e. the
        surgeon actually opened Segment Editor since the segmentation was
        last (re)built.

        History (2026-07-27, all same day): this WAS unconditional (ran on
        every "Next" click regardless of edits). A same-day attempt to
        gate it was reverted, on the theory that Isolate Patch depended on
        this exact double-processing to reliably separate from the rest
        of the head. That theory was then DISPROVEN directly: Thomas
        confirmed the segmentation is watertight and hole-free
        immediately after Run Segmentation, and reproducibly gets 2 holes
        (one in the helix, one at the top of the blob) specifically after
        clicking Next -- i.e. specifically from THIS re-export/postprocess
        round trip, not from the original segmentation. Gated back to
        conditional per that direct evidence: skipping this when nothing
        was edited keeps the mesh exactly as built (and confirmed
        watertight) by _on_run_clicked(), avoiding the round trip that
        was demonstrably creating the holes. If Segment Editor WAS used,
        the freshly hand-edited content hasn't been through any
        postprocessing yet, so re-deriving and postprocessing once here
        is still correct and necessary."""
        import slicer
        import sitkUtils

        segmentation_node = self.state.pinna_region_segmentation_node
        if segmentation_node is None:
            return True, ""

        temp_label_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "PinnaRegionEdited"
        )
        slicer.modules.segmentations.logic().ExportVisibleSegmentsToLabelmapNode(
            segmentation_node, temp_label_node
        )
        sitk_image = io_utils.flip_ras_lps(sitkUtils.PullVolumeFromSlicer(temp_label_node))
        slicer.mrmlScene.RemoveNode(temp_label_node)

        # See _on_run_clicked()/run_blocking()'s docstring in base_page.py
        # for why these run on a background thread while a progress bar
        # shows here -- this same postprocess+meshing round trip runs
        # whenever the surgeon clicks Next after touching up in Segment
        # Editor, so it deserves the same "don't look frozen" treatment as
        # the Run button's own pipeline.
        self.ui.progressBar.setVisible(True)
        self.ui.progressBar.setMinimum(0)
        self.ui.progressBar.setMaximum(4)
        self.ui.progressBar.setValue(0)
        try:
            sitk_image = self.run_blocking(
                lambda: postprocess.run_full_postprocess(sitk_image),
                status_text="Cleaning up your edited segmentation...",
            )
            self.ui.progressBar.setValue(1)
            sitk_image = self.run_blocking(
                lambda: roi_crop.crop_to_own_bounding_box(sitk_image, config.PINNA_TIGHT_CROP_MARGIN_MM),
                status_text="Cropping to tight bounding box...",
            )
            self.ui.progressBar.setValue(2)

            try:
                mesh = self.run_blocking(
                    lambda: mesh_export.label_map_to_mesh(sitk_image),
                    status_text="Building 3D surface mesh...",
                )
            except mesh_export.EmptySegmentationError:
                return False, (
                    "The segmentation is empty after your Segment Editor edits. "
                    "Go back to Segment Editor and add material back, or "
                    "re-run the automatic segmentation."
                )
            self.ui.progressBar.setValue(3)
            mesh = self.run_blocking(
                lambda: mesh_export.decimate_to_target_resolution(mesh),
                status_text="Simplifying mesh...",
            )
            self.ui.progressBar.setValue(4)
        finally:
            self.ui.progressBar.setVisible(False)

        mesh_export.export_mesh(mesh, self.state.pinna_region_mesh_path)
        if self.state.pinna_region_model_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.pinna_region_model_node)
        self.state.pinna_region_model_node = slicer.util.loadModel(
            self.state.pinna_region_mesh_path
        )
        self.state.pinna_region_model_node.GetDisplayNode().SetVisibility(True)
        # Hide the segmentation again now that the refreshed model node is
        # showing the same thing -- keeps it from visually duplicating the
        # drawing model on the next page.
        segmentation_node.GetDisplayNode().SetVisibility(False)
        return True, ""

    def _on_reset_page_clicked(self):
        wizard_state.clear_page_state(self.state, "pinna_review")
        self.ui.skinThresholdSlider.value = config.SKIN_AIR_THRESHOLD
        self.ui.openSegmentEditorButton.setEnabled(False)
        self.ui.statusLabel.setText("Segmentation cleared. Adjust the slider if needed, then click Run.")

    def _on_revert_to_here_clicked(self):
        import slicer

        if wizard_state.has_downstream_state(self.state, "pinna_review"):
            if not slicer.util.confirmYesNoDisplay(
                "This will clear every step after this one (the drawn "
                "pinna outline, the scutum segmentation and outline, "
                "verification, and the heatmap result). This segmentation "
                "is kept. Continue?"
            ):
                return
            wizard_state.clear_downstream_state(self.state, "pinna_review")
        self.ui.statusLabel.setText(
            "Later steps cleared. Go to Next when ready to redo them."
        )

    def on_leave_next(self):
        if self.state.pinna_region_mesh_path is None:
            return False, "Please run the segmentation before continuing."
        # Only re-derive the mesh from the segmentation if Segment Editor
        # was actually used -- confirmed directly (2026-07-27, real scan)
        # that this re-derivation is what creates holes in the mesh, not
        # the original segmentation, so skip it entirely when nothing
        # changed. See _refresh_mesh_from_segmentation's docstring.
        if getattr(self, "_segmentation_edited", False):
            return self._refresh_mesh_from_segmentation()
        return True, ""
