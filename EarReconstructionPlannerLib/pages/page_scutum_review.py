"""
page_scutum_review.py
========================
Page 3: run the bone-wall segmentation and let the surgeon review/adjust
it before moving on.

Two sliders (air threshold, bone threshold) mirror config.py's tunable
values -- moving them re-runs the fast threshold-based segmentation
(core/segment_dl.py, which itself may use a trained model later, or falls
back to core/segment_threshold.py today). "Open Segment Editor" hands the
result to Slicer's own, surgeon-familiar Segment Editor for manual
touch-ups (painting/erasing), rather than reinventing that tool.

Expected widgets in page_scutum_review.ui:
  - airThresholdSlider   (QSlider or ctkSliderWidget)
  - boneThresholdSlider  (QSlider or ctkSliderWidget)
  - runButton            (QPushButton)
  - openSegmentEditorButton (QPushButton)
  - statusLabel          (QLabel)
"""

from __future__ import annotations
import os
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from core import roi_crop, segment_dl, postprocess, mesh_export
import config


class ScutumReviewPage(WizardPage):
    def on_enter(self):
        self.ui.airThresholdSlider.minimum = config.AIR_THRESHOLD_ADJUST_RANGE[0]
        self.ui.airThresholdSlider.maximum = config.AIR_THRESHOLD_ADJUST_RANGE[1]
        self.ui.airThresholdSlider.value = config.DEFAULT_AIR_THRESHOLD

        self.ui.boneThresholdSlider.minimum = config.BONE_THRESHOLD_ADJUST_RANGE[0]
        self.ui.boneThresholdSlider.maximum = config.BONE_THRESHOLD_ADJUST_RANGE[1]
        self.ui.boneThresholdSlider.value = config.DEFAULT_BONE_THRESHOLD

        self.ui.runButton.clicked.connect(self._on_run_clicked)
        self.ui.openSegmentEditorButton.clicked.connect(self._on_open_segment_editor_clicked)
        self.ui.openSegmentEditorButton.setEnabled(False)
        self.ui.statusLabel.setText("Adjust the sliders if needed, then click Run.")

    def _on_run_clicked(self):
        import sitkUtils
        import slicer

        if self.state.volume_node is None:
            self.ui.statusLabel.setText("No scan loaded -- go back and select one first.")
            return

        self.ui.statusLabel.setText("Segmenting bone wall...")
        slicer.app.processEvents()

        sitk_image = sitkUtils.PullVolumeFromSlicer(self.state.volume_node)

        # Cheap rectangular pre-crop around the landmarks BEFORE building
        # the precise cylinder ROI mask -- build_roi_mask() evaluates every
        # voxel in whatever image it's given, which is fine on an already-
        # small volume but would try to allocate an array the size of the
        # entire scan otherwise. See roi_crop.crop_to_landmark_region's
        # docstring for details.
        coarse_cropped = roi_crop.crop_to_landmark_region(
            sitk_image, self.state.scutum_landmarks
        )

        roi_mask = roi_crop.build_roi_mask(coarse_cropped, self.state.scutum_landmarks)
        cropped_image = roi_crop.crop_to_roi_bounding_box(coarse_cropped, roi_mask)
        cropped_roi_mask = roi_crop.crop_to_roi_bounding_box(roi_mask, roi_mask)

        bone_wall = segment_dl.segment(
            cropped_image,
            cropped_roi_mask,
            self.state.scutum_landmarks,
            air_threshold=self.ui.airThresholdSlider.value,
            bone_threshold=self.ui.boneThresholdSlider.value,
        )
        bone_wall = postprocess.run_full_postprocess(bone_wall)

        # Push into Slicer as a labelmap node so the surgeon can see it in
        # the slice/3D views and hand it to Segment Editor.
        label_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "ScutumBoneWall"
        )
        sitkUtils.PushVolumeToSlicer(bone_wall, label_node)
        self.state.scutum_bone_wall_label_node = label_node

        # Also export a mesh now -- the draw page (next-but-one) needs an
        # actual surface to draw on, and re-running mesh export there would
        # duplicate this work.
        try:
            mesh = mesh_export.label_map_to_mesh(bone_wall)
        except mesh_export.EmptySegmentationError:
            self.ui.openSegmentEditorButton.setEnabled(False)
            self.ui.statusLabel.setText(
                "No bone wall found with these settings. Try lowering the "
                "bone threshold or adjusting the air threshold, or go back "
                "and double-check the landmark placement. (The empty "
                "segmentation is still visible in the slice views.)"
            )
            return

        mesh_path = os.path.join(
            self.state.working_dir or slicer.app.temporaryPath, "scutum_bone_wall.stl"
        )
        mesh_export.export_mesh(mesh, mesh_path)
        self.state.scutum_bone_wall_mesh_path = mesh_path

        if self.state.scutum_bone_wall_model_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.scutum_bone_wall_model_node)
        self.state.scutum_bone_wall_model_node = slicer.util.loadModel(mesh_path)

        self.ui.openSegmentEditorButton.setEnabled(True)
        self.ui.statusLabel.setText(
            "Segmentation complete. Review it in the 3D view, adjust sliders "
            "and re-run if needed, or open Segment Editor for manual touch-ups."
        )

    def _on_open_segment_editor_clicked(self):
        import slicer
        slicer.util.selectModule("SegmentEditor")

    def on_leave_next(self):
        if self.state.scutum_bone_wall_mesh_path is None:
            return False, "Please run the segmentation before continuing."
        return True, ""
