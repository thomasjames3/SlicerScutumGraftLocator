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
  - tutorialLabel        (QLabel) -- extra guidance, shown only in tutorial mode
  - airThresholdSlider   (QSlider or ctkSliderWidget)
  - boneThresholdSlider  (QSlider or ctkSliderWidget)
  - runButton            (QPushButton)
  - openSegmentEditorButton (QPushButton)
  - resetPageButton      (QPushButton) -- clear this page's own segmentation
  - revertToHereButton   (QPushButton) -- clear every later step, keep this segmentation
  - statusLabel          (QLabel)
"""

from __future__ import annotations
import os
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from EarReconstructionPlannerLib import wizard_state
from core import roi_crop, segment_dl, postprocess, mesh_export, io_utils
import config


class ScutumReviewPage(WizardPage):
    def on_enter(self):
        self.set_tutorial_text(
            "This step automatically finds the bone wall of the ear canal "
            "near the 2 points you placed. The two sliders control how "
            "sensitive that search is:\n"
            "- Air threshold: how dark a voxel must be to count as air (the "
            "hollow part of the canal).\n"
            "- Bone threshold: how bright a voxel must be to count as bone.\n"
            "You usually don't need to change these -- click 'Run "
            "Segmentation' first with the defaults and see what comes out.\n\n"
            "After running, look at the result in the 3D view (left-drag to "
            "rotate, middle-drag to pan, scroll or right-drag to zoom). The "
            "3D view has its own small toolbar in the top-left corner: the "
            "recenter button (a crosshair/target icon) reframes the camera "
            "around whatever's currently visible, handy if you've rotated/"
            "zoomed somewhere confusing. The small R/L/A/P/S/I axis-letter "
            "widget in the corner of the 3D view lets you click a letter to "
            "snap the camera to look from exactly that direction, which is "
            "useful for judging the shape consistently.\n\n"
            "If it looks wrong (missing wall, too much extra material, or "
            "the status message says nothing was found), drag a slider to "
            "adjust and click 'Run Segmentation' again -- you can do this "
            "as many times as you like.\n\n"
            "If the result is close but has small gaps or stray bits even "
            "after adjusting the sliders, click 'Open Segment Editor for "
            "Manual Touch-Up'. That opens Slicer's own painting tool: pick "
            "the 'Paint' or 'Erase' effect on the left, adjust the brush "
            "size, then click or drag over the volume in a slice view to add "
            "or remove material by hand. Come back to this module (the "
            "modules dropdown at the top) when you're done."
        )
        self.ui.airThresholdSlider.minimum = config.AIR_THRESHOLD_ADJUST_RANGE[0]
        self.ui.airThresholdSlider.maximum = config.AIR_THRESHOLD_ADJUST_RANGE[1]
        self.ui.airThresholdSlider.value = config.DEFAULT_AIR_THRESHOLD

        self.ui.boneThresholdSlider.minimum = config.BONE_THRESHOLD_ADJUST_RANGE[0]
        self.ui.boneThresholdSlider.maximum = config.BONE_THRESHOLD_ADJUST_RANGE[1]
        self.ui.boneThresholdSlider.value = config.DEFAULT_BONE_THRESHOLD

        self.ui.runButton.clicked.connect(self._on_run_clicked)
        self.ui.openSegmentEditorButton.clicked.connect(self._on_open_segment_editor_clicked)
        self.ui.resetPageButton.clicked.connect(self._on_reset_page_clicked)
        self.ui.revertToHereButton.clicked.connect(self._on_revert_to_here_clicked)
        self.ui.openSegmentEditorButton.setEnabled(self.state.scutum_bone_wall_mesh_path is not None)
        self.ui.statusLabel.setText("Adjust the sliders if needed, then click Run.")

    def _on_run_clicked(self):
        import sitkUtils
        import slicer

        if self.state.volume_node is None:
            self.ui.statusLabel.setText("No scan loaded -- go back and select one first.")
            return

        # Re-running this segmentation invalidates anything built on top of
        # the old one (a defect outline drawn on the old bone-wall mesh,
        # the verify approval, the heatmap) -- clear it up front so a
        # surgeon adjusting thresholds and re-running can never end up
        # silently carrying forward a now-mismatched downstream result.
        wizard_state.clear_downstream_state(self.state, "scutum_review")

        self.ui.statusLabel.setText("Segmenting bone wall...")
        slicer.app.processEvents()

        # PullVolumeFromSlicer() returns the image in plain ITK/LPS
        # convention, but scutum_landmarks were captured in Slicer's own
        # RAS convention -- flip so every physical-coordinate calculation
        # below (ROI cropping, axis math) operates in the same space the
        # landmarks are in. See io_utils.flip_ras_lps's docstring for why
        # this matters: without it, the ROI ends up mirrored across the
        # sagittal/coronal planes from where the surgeon actually clicked.
        sitk_image = io_utils.flip_ras_lps(sitkUtils.PullVolumeFromSlicer(self.state.volume_node))

        # Cheap rectangular pre-crop around the landmarks BEFORE building
        # the precise cylinder ROI mask -- build_roi_mask() evaluates every
        # voxel in whatever image it's given, which is fine on an already-
        # small volume but would try to allocate an array the size of the
        # entire scan otherwise. See roi_crop.crop_to_landmark_region's
        # docstring for details.
        coarse_cropped = roi_crop.crop_to_landmark_region(
            sitk_image, self.state.scutum_landmarks
        )

        try:
            roi_mask = roi_crop.build_roi_mask(coarse_cropped, self.state.scutum_landmarks)
        except ValueError as exc:
            self.ui.statusLabel.setText(str(exc))
            return
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
        # bone_wall is RAS-consistent (inherited from the flipped
        # sitk_image above); flip back to LPS since PushVolumeToSlicer
        # expects plain ITK convention and converts LPS->RAS itself.
        sitkUtils.PushVolumeToSlicer(io_utils.flip_ras_lps(bone_wall), label_node)
        self.state.scutum_bone_wall_label_node = label_node

        # Also export a mesh now -- the draw page (next-but-one) needs an
        # actual surface to draw on, and re-running mesh export there would
        # duplicate this work. Uses the still-RAS-consistent `bone_wall`
        # (not the flipped copy just pushed above) so the exported mesh's
        # vertices are in RAS and line up correctly when loaded back into
        # Slicer alongside the volume.
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

        # Hide the landmark points now that the model exists -- they've
        # served their purpose, and otherwise sit right on top of the mesh
        # and get in the way of clicking to draw the outline on the next
        # page.
        if self.state.scutum_landmarks_fiducial_node is not None:
            self.state.scutum_landmarks_fiducial_node.GetDisplayNode().SetVisibility(False)

        # Recenter the 3D view on the new model and orient the camera to
        # look from the correct side for this ear (self.state.ear_side) --
        # see base_page.WizardPage.recenter_3d_view() for why this can't
        # just always look from the Right.
        self.recenter_3d_view()

        self.ui.openSegmentEditorButton.setEnabled(True)
        self.ui.statusLabel.setText(
            "Segmentation complete. Review it in the 3D view, adjust sliders "
            "and re-run if needed, or open Segment Editor for manual touch-ups."
        )

    def _on_open_segment_editor_clicked(self):
        import slicer
        slicer.util.selectModule("SegmentEditor")

    def _on_reset_page_clicked(self):
        wizard_state.clear_page_state(self.state, "scutum_review")
        self.ui.airThresholdSlider.value = config.DEFAULT_AIR_THRESHOLD
        self.ui.boneThresholdSlider.value = config.DEFAULT_BONE_THRESHOLD
        self.ui.openSegmentEditorButton.setEnabled(False)
        self.ui.statusLabel.setText("Segmentation cleared. Adjust the sliders if needed, then click Run.")

    def _on_revert_to_here_clicked(self):
        import slicer

        if wizard_state.has_downstream_state(self.state, "scutum_review"):
            if not slicer.util.confirmYesNoDisplay(
                "This will clear every step after this one (the drawn "
                "defect outline, verification, and the heatmap result). "
                "This segmentation is kept. Continue?"
            ):
                return
            wizard_state.clear_downstream_state(self.state, "scutum_review")
        self.ui.statusLabel.setText(
            "Later steps cleared. Go to Next when ready to redo them."
        )

    def on_leave_next(self):
        if self.state.scutum_bone_wall_mesh_path is None:
            return False, "Please run the segmentation before continuing."
        return True, ""
