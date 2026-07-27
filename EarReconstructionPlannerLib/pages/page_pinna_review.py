"""
page_pinna_review.py
=======================
Page 6: run the pinna region (skin surface) segmentation and let the
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
  - statusLabel               (QLabel)
"""

from __future__ import annotations
import os
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from EarReconstructionPlannerLib import wizard_state
from core import roi_crop, segment_pinna_threshold, postprocess, mesh_export, io_utils
import config


class PinnaReviewPage(WizardPage):
    def on_enter(self):
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

        # Same coarse pre-crop fix as the scutum review page -- see
        # roi_crop.crop_to_point_region's docstring for why this has to
        # happen before build_spherical_roi_mask() on a real, full-
        # resolution scan.
        coarse_cropped = roi_crop.crop_to_point_region(
            sitk_image,
            self.state.pinna_landmarks.ear_center,
            radius_mm=config.PINNA_ROI_RADIUS_MM,
        )

        roi_mask = roi_crop.build_spherical_roi_mask(
            coarse_cropped,
            self.state.pinna_landmarks.ear_center,
            radius_mm=config.PINNA_ROI_RADIUS_MM,
        )
        cropped_image = roi_crop.crop_to_roi_bounding_box(coarse_cropped, roi_mask)
        cropped_roi_mask = roi_crop.crop_to_roi_bounding_box(roi_mask, roi_mask)

        region_mask = segment_pinna_threshold.segment_pinna_region(
            cropped_image,
            cropped_roi_mask,
            self.state.pinna_landmarks,
            threshold=self.ui.skinThresholdSlider.value,
        )
        region_mask = postprocess.run_full_postprocess(region_mask)

        # Push into a segmentation node (not a plain labelmap) so it's
        # something Segment Editor can actually operate on -- see the
        # matching comment in page_scutum_review.py for why. The labelmap
        # node here is just a throwaway bridge for the conversion Slicer's
        # segmentations logic expects; it's removed once the segmentation
        # node owns the data.
        temp_label_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "PinnaRegionTemp"
        )
        # region_mask is RAS-consistent; flip back to LPS since
        # PushVolumeToSlicer expects plain ITK convention (see
        # page_scutum_review.py for the matching comment).
        sitkUtils.PushVolumeToSlicer(io_utils.flip_ras_lps(region_mask), temp_label_node)

        if self.state.pinna_region_segmentation_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.pinna_region_segmentation_node)
        segmentation_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentationNode", "PinnaRegionSegmentation"
        )
        segmentation_node.CreateDefaultDisplayNodes()
        slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
            temp_label_node, segmentation_node
        )
        slicer.mrmlScene.RemoveNode(temp_label_node)
        # Hidden by default -- see the matching comment in
        # page_scutum_review.py for why (avoids visually duplicating the
        # exported model node the surgeon actually draws on).
        segmentation_node.GetDisplayNode().SetVisibility(False)
        self.state.pinna_region_segmentation_node = segmentation_node

        # Uses the still-RAS-consistent `region_mask` so the exported
        # mesh's vertices line up correctly when loaded back into Slicer.
        mesh = mesh_export.label_map_to_mesh(region_mask)
        mesh_path = os.path.join(
            self.state.working_dir or slicer.app.temporaryPath, "pinna_region.stl"
        )
        mesh_export.export_mesh(mesh, mesh_path)
        self.state.pinna_region_mesh_path = mesh_path

        if self.state.pinna_region_model_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.pinna_region_model_node)
        self.state.pinna_region_model_node = slicer.util.loadModel(mesh_path)

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
        contents -- see the matching method in page_scutum_review.py for
        the full reasoning. Called every time Next is clicked."""
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
        sitk_image = postprocess.run_full_postprocess(sitk_image)

        try:
            mesh = mesh_export.label_map_to_mesh(sitk_image)
        except mesh_export.EmptySegmentationError:
            return False, (
                "The segmentation is empty after your Segment Editor edits. "
                "Go back to Segment Editor and add material back, or "
                "re-run the automatic segmentation."
            )

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
                "pinna outline, verification, and the heatmap result). "
                "This segmentation is kept. Continue?"
            ):
                return
            wizard_state.clear_downstream_state(self.state, "pinna_review")
        self.ui.statusLabel.setText(
            "Later steps cleared. Go to Next when ready to redo them."
        )

    def on_leave_next(self):
        if self.state.pinna_region_mesh_path is None:
            return False, "Please run the segmentation before continuing."
        return self._refresh_mesh_from_segmentation()
