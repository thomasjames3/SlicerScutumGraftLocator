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
  - skinThresholdSlider       (QSlider or ctkSliderWidget)
  - runButton                 (QPushButton)
  - openSegmentEditorButton  (QPushButton)
  - statusLabel               (QLabel)
"""

from __future__ import annotations
import os
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from core import roi_crop, segment_pinna_threshold, postprocess, mesh_export, io_utils
import config


class PinnaReviewPage(WizardPage):
    def on_enter(self):
        self.ui.skinThresholdSlider.minimum = config.SKIN_THRESHOLD_ADJUST_RANGE[0]
        self.ui.skinThresholdSlider.maximum = config.SKIN_THRESHOLD_ADJUST_RANGE[1]
        self.ui.skinThresholdSlider.value = config.SKIN_AIR_THRESHOLD

        self.ui.runButton.clicked.connect(self._on_run_clicked)
        self.ui.openSegmentEditorButton.clicked.connect(self._on_open_segment_editor_clicked)
        self.ui.openSegmentEditorButton.setEnabled(False)
        self.ui.statusLabel.setText("Adjust the slider if needed, then click Run.")

    def _on_run_clicked(self):
        import sitkUtils
        import slicer

        if self.state.volume_node is None:
            self.ui.statusLabel.setText("No scan loaded -- go back and select one first.")
            return

        self.ui.statusLabel.setText("Segmenting skin surface near the ear...")
        slicer.app.processEvents()

        # See page_scutum_review.py / io_utils.flip_ras_lps for why this
        # conversion is required: PullVolumeFromSlicer() returns the image
        # in plain ITK/LPS convention, but pinna_landmarks.ear_center was
        # captured in Slicer's own RAS convention.
        sitk_image = io_utils.flip_ras_lps(sitkUtils.PullVolumeFromSlicer(self.state.volume_node))

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

        label_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "PinnaRegion"
        )
        # region_mask is RAS-consistent; flip back to LPS since
        # PushVolumeToSlicer expects plain ITK convention (see
        # page_scutum_review.py for the matching comment).
        sitkUtils.PushVolumeToSlicer(io_utils.flip_ras_lps(region_mask), label_node)
        self.state.pinna_region_label_node = label_node

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
        import ctk

        threeDView = slicer.app.layoutManager().threeDWidget(0).threeDView()
        threeDView.resetFocalPoint()
        threeDView.resetCamera()
        threeDView.lookFromAxis(ctk.ctkAxesWidget.Right)

        self.ui.openSegmentEditorButton.setEnabled(True)
        self.ui.statusLabel.setText(
            "Segmentation complete. Review it in the 3D view, adjust the "
            "slider and re-run if needed, or open Segment Editor for manual touch-ups."
        )

    def _on_open_segment_editor_clicked(self):
        import slicer
        slicer.util.selectModule("SegmentEditor")

    def on_leave_next(self):
        if self.state.pinna_region_mesh_path is None:
            return False, "Please run the segmentation before continuing."
        return True, ""
