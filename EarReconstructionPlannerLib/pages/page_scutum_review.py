"""
page_scutum_review.py
========================
Page 6: run the bone-wall segmentation and let the surgeon review/adjust
it before moving on.

Two sliders (air threshold, bone threshold) mirror config.py's tunable
values -- moving them re-runs the fast threshold-based segmentation
(core/segment_dl.py, which itself may use a trained model later, or falls
back to core/segment_threshold.py today). "Open Segment Editor" hands the
result to Slicer's own, surgeon-familiar Segment Editor for manual
touch-ups (painting/erasing), rather than reinventing that tool.

Expected widgets in page_scutum_review.ui:
  - tutorialLabel        (QLabel) -- extra guidance, shown only in tutorial mode
  - calibrationStepProgressLabel (QLabel) -- e.g. "Optional calibration: 0 of 3"
  - calibrationInstructionLabel  (QLabel) -- current calibration step's instruction
  - placeCalibrationPointButton  (QPushButton)
  - redoCalibrationButton        (QPushButton) -- clear just the 3 calibration points
  - airThresholdSlider   (QSlider or ctkSliderWidget)
  - boneThresholdSlider  (QSlider or ctkSliderWidget)
  - runButton            (QPushButton)
  - openSegmentEditorButton (QPushButton)
  - statusLabel          (QLabel)
  - wallThicknessWarningLabel (QLabel) -- advisory-only, shown if the segmented wall looks too thin to trust
  - resetPageButton      (QPushButton) -- clear this page's own segmentation
  - revertToHereButton   (QPushButton) -- clear every later step, keep this segmentation

Optional seed-click calibration (core/threshold_seeds.py): the surgeon can
click 3 points (air / bone / soft tissue) before running, which pre-fills
the two sliders below with per-scan-calibrated starting values. This is
purely a convenience -- the sliders remain the actual source of truth and
stay manually adjustable either way. See config.py's "seed-based threshold
calibration" section for why this helps.

Post-segmentation thin-wall check (core/wall_quality.py): after Run (or
after a Segment Editor edit is re-baked), the resulting wall is checked
for suspiciously-thin regions and flagged via wallThicknessWarningLabel if
found. Advisory only -- never blocks Next. See config.py's "post-
segmentation wall-thickness warning" section for why no threshold choice
can fix this failure mode.
"""

from __future__ import annotations
import os
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from EarReconstructionPlannerLib import wizard_state
from core import roi_crop, segment_dl, postprocess, mesh_export, io_utils, threshold_seeds, wall_quality
from core.threshold_seeds import SEED_STEPS, ThresholdSeeds
import config


class ScutumReviewPage(WizardPage):
    def on_enter(self):
        # The scutum stage resumes here after the whole pinna stage (see
        # "Pinna-first wizard reorder" in CLAUDE.md) -- hide the pinna's
        # finished models/landmarks now, since they were left visible from
        # that earlier stage and would otherwise clutter the 3D view while
        # reviewing the bone-wall segmentation. The scutum ear-canal-axis
        # points stay hidden too (they were hidden during the pinna stage by
        # page_pinna_landmarks.py's on_enter) -- they're only used
        # programmatically by the segmentation math below, not as a visual
        # reference, so there's no reason to clutter the view with them here.
        if self.state.pinna_region_model_node is not None:
            self.state.pinna_region_model_node.GetDisplayNode().SetVisibility(False)
        if self.state.pinna_isolated_model_node is not None:
            self.state.pinna_isolated_model_node.GetDisplayNode().SetVisibility(False)
        if self.state.pinna_landmarks_fiducial_node is not None:
            self.state.pinna_landmarks_fiducial_node.GetDisplayNode().SetVisibility(False)
        if self.state.scutum_landmarks_fiducial_node is not None:
            self.state.scutum_landmarks_fiducial_node.GetDisplayNode().SetVisibility(False)

        self._setup_calibration_seeds()

        self.set_tutorial_text(
            "Before running, you can optionally click 'Place Calibration "
            "Point' three times -- once inside the air-filled canal, once "
            "on bone, once on soft tissue -- to automatically fill in good "
            "starting values for the two sliders below, tuned to this "
            "specific scan. This step is optional; the default slider "
            "values work reasonably well without it. If a point lands in "
            "the wrong spot, use 'Redo Calibration Points' to start over.\n\n"
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
            "modules dropdown at the top) when you're done.\n\n"
            "If a message appears after running saying the wall looks very "
            "thin in places, it means the scan's resolution may not be fine "
            "enough to reliably show the true wall thickness there. You can "
            "still continue, but treat that area with extra caution and "
            "consider a manual double-check."
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
        self.ui.placeCalibrationPointButton.clicked.connect(self._on_place_calibration_point_clicked)
        self.ui.redoCalibrationButton.clicked.connect(self._on_redo_calibration_clicked)
        self.ui.openSegmentEditorButton.setEnabled(self.state.scutum_bone_wall_mesh_path is not None)
        self.ui.wallThicknessWarningLabel.setText("")
        self.ui.wallThicknessWarningLabel.setVisible(False)
        self.ui.statusLabel.setText("Adjust the sliders if needed, then click Run.")

    def _setup_calibration_seeds(self):
        import slicer

        # Reuse the fiducial node from a previous visit if it's still live,
        # same reasoning as page_scutum_landmarks.py's on_enter -- creating
        # a fresh node on every re-entry would orphan earlier visits' nodes
        # and always restart the step counter at 0.
        existing = self.state.scutum_threshold_seeds_fiducial_node
        if existing is not None and slicer.mrmlScene.IsNodePresent(existing):
            self._seed_fiducial_node = existing
        else:
            self._seed_fiducial_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode", "ScutumThresholdSeeds"
            )
            self._seed_fiducial_node.SetLocked(False)
            self._seed_fiducial_node.CreateDefaultDisplayNodes()
            self.state.scutum_threshold_seeds_fiducial_node = self._seed_fiducial_node
        self._seed_fiducial_node.GetDisplayNode().SetVisibility(True)

        # Resume at whichever step matches what's already been placed.
        self._current_calib_step = 0
        for step in SEED_STEPS:
            if getattr(self.state.scutum_threshold_seeds, step["field"]) is None:
                break
            self._current_calib_step += 1

        self._calib_observer_tag = None
        self._update_calibration_step_display()

    def _update_calibration_step_display(self):
        if self._current_calib_step >= len(SEED_STEPS):
            self.ui.calibrationInstructionLabel.setText(f"All {len(SEED_STEPS)} calibration points placed.")
            self.ui.calibrationStepProgressLabel.setText(
                f"Optional calibration: {len(SEED_STEPS)} of {len(SEED_STEPS)}"
            )
            self.ui.placeCalibrationPointButton.setEnabled(False)
        else:
            step = SEED_STEPS[self._current_calib_step]
            self.ui.calibrationInstructionLabel.setText(step["instruction"])
            self.ui.calibrationStepProgressLabel.setText(
                f"Optional calibration: {self._current_calib_step} of {len(SEED_STEPS)}"
            )
            self.ui.placeCalibrationPointButton.setEnabled(True)

    def _on_place_calibration_point_clicked(self):
        import slicer

        interaction_node = slicer.app.applicationLogic().GetInteractionNode()
        selection_node = slicer.app.applicationLogic().GetSelectionNode()
        selection_node.SetActivePlaceNodeID(self._seed_fiducial_node.GetID())
        interaction_node.SetCurrentInteractionMode(interaction_node.Place)
        interaction_node.SetPlaceModePersistence(0)  # one point, then back to normal mode

        self._calib_observer_tag = self._seed_fiducial_node.AddObserver(
            self._seed_fiducial_node.PointPositionDefinedEvent, self._on_calibration_point_placed
        )
        self.ui.placeCalibrationPointButton.setEnabled(False)
        self.ui.statusLabel.setText("Click a point in the 3D view or on a slice...")

    def _on_calibration_point_placed(self, caller, event):
        if self._calib_observer_tag is not None:
            self._seed_fiducial_node.RemoveObserver(self._calib_observer_tag)
            self._calib_observer_tag = None

        point_index = self._seed_fiducial_node.GetNumberOfControlPoints() - 1
        ras = [0.0, 0.0, 0.0]
        self._seed_fiducial_node.GetNthControlPointPositionWorld(point_index, ras)

        field_name = SEED_STEPS[self._current_calib_step]["field"]
        setattr(self.state.scutum_threshold_seeds, field_name, tuple(ras))
        self._seed_fiducial_node.SetNthControlPointLabel(point_index, field_name)

        self._current_calib_step += 1
        self._update_calibration_step_display()

        if self.state.scutum_threshold_seeds.is_complete():
            self._run_calibration()

    def _run_calibration(self):
        import sitkUtils

        if self.state.volume_node is None:
            self.ui.statusLabel.setText("No scan loaded -- go back and select one first.")
            return

        sitk_image = io_utils.flip_ras_lps(sitkUtils.PullVolumeFromSlicer(self.state.volume_node))
        coarse_cropped = roi_crop.crop_to_landmark_region(sitk_image, self.state.scutum_landmarks)

        try:
            air_threshold, bone_threshold = threshold_seeds.calibrate_thresholds(
                coarse_cropped, self.state.scutum_threshold_seeds
            )
        except ValueError as exc:
            self.ui.statusLabel.setText(str(exc))
            return

        self.ui.airThresholdSlider.value = air_threshold
        self.ui.boneThresholdSlider.value = bone_threshold

        warning = self.state.scutum_threshold_seeds.validate() or threshold_seeds.check_seed_plausibility(
            coarse_cropped, self.state.scutum_threshold_seeds
        )
        self.ui.statusLabel.setText(
            warning
            or "Calibration points placed -- thresholds below were pre-filled. "
            "Adjust the sliders if needed, then click Run Segmentation."
        )

    def _on_redo_calibration_clicked(self):
        if self._seed_fiducial_node is not None:
            self._seed_fiducial_node.RemoveAllControlPoints()
        self.state.scutum_threshold_seeds = ThresholdSeeds()
        self._current_calib_step = 0
        self._update_calibration_step_display()
        self.ui.statusLabel.setText(
            "Calibration points cleared. Place them again, or adjust the sliders directly."
        )

    def _check_and_display_wall_thickness(self, bone_wall_sitk_image):
        warning = wall_quality.check_wall_thickness(bone_wall_sitk_image)
        self.ui.wallThicknessWarningLabel.setText(warning or "")
        self.ui.wallThicknessWarningLabel.setVisible(bool(warning))

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

        self.ui.wallThicknessWarningLabel.setText("")
        self.ui.wallThicknessWarningLabel.setVisible(False)
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

        raw_bone_wall, intensity_override_mask = segment_dl.segment(
            cropped_image,
            cropped_roi_mask,
            self.state.scutum_landmarks,
            air_threshold=self.ui.airThresholdSlider.value,
            bone_threshold=self.ui.boneThresholdSlider.value,
        )
        # Kept alongside the postprocessed result -- mesh_export.
        # label_map_to_mesh_subvoxel() needs both, to tell which voxels
        # postprocessing actually changed (see its docstring).
        bone_wall = postprocess.run_full_postprocess(raw_bone_wall, close_tunnels=True)
        self._check_and_display_wall_thickness(bone_wall)

        # Push into a segmentation node (not a plain labelmap) so it's
        # something Segment Editor can actually operate on -- Segment
        # Editor edits vtkMRMLSegmentationNodes, not label map volumes
        # directly. The labelmap node here is just a throwaway bridge for
        # the conversion Slicer's segmentations logic expects; it's
        # removed once the segmentation node owns the data.
        temp_label_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "ScutumBoneWallTemp"
        )
        # bone_wall is RAS-consistent (inherited from the flipped
        # sitk_image above); flip back to LPS since PushVolumeToSlicer
        # expects plain ITK convention and converts LPS->RAS itself.
        sitkUtils.PushVolumeToSlicer(io_utils.flip_ras_lps(bone_wall), temp_label_node)

        if self.state.scutum_bone_wall_segmentation_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.scutum_bone_wall_segmentation_node)
        segmentation_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentationNode", "ScutumBoneWallSegmentation"
        )
        segmentation_node.CreateDefaultDisplayNodes()
        slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
            temp_label_node, segmentation_node
        )
        slicer.mrmlScene.RemoveNode(temp_label_node)
        # Hidden by default -- the exported/smoothed model node below is
        # what the surgeon sees and draws on in the 3D view. Without this,
        # the segmentation's own auto-generated 3D surface would sit right
        # on top of that model as a visually-duplicated, unsmoothed copy.
        # Segment Editor's own slice-view painting doesn't need this node
        # visible to work on it.
        segmentation_node.GetDisplayNode().SetVisibility(False)
        self.state.scutum_bone_wall_segmentation_node = segmentation_node
        # This freshly-built segmentation already went through
        # run_full_postprocess() once, right above -- see
        # _refresh_mesh_from_segmentation()'s matching comment for why
        # this flag exists.
        self._segmentation_edited = False

        # Also export a mesh now -- the draw page (next-but-one) needs an
        # actual surface to draw on, and re-running mesh export there would
        # duplicate this work. Uses the still-RAS-consistent `bone_wall`
        # (not the flipped copy just pushed above) so the exported mesh's
        # vertices are in RAS and line up correctly when loaded back into
        # Slicer alongside the volume.
        #
        # Uses the sub-voxel extraction (real smoothed intensity near the
        # boundary, not the already-binarized mask) since this mesh comes
        # straight from thresholding `cropped_image` at boneThresholdSlider's
        # value -- there's a real isovalue to extract against. This is NOT
        # used in _refresh_mesh_from_segmentation() below: after a Segment
        # Editor hand-edit, the mask boundary is whatever the surgeon
        # painted, with no single threshold it corresponds to, so that path
        # correctly keeps using plain label_map_to_mesh().
        try:
            mesh = mesh_export.label_map_to_mesh_subvoxel(
                cropped_image,
                bone_wall,
                raw_bone_wall,
                self.ui.boneThresholdSlider.value,
                intensity_override_mask=intensity_override_mask,
            )
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
        # page. Same reasoning for the calibration seed points, if any were
        # placed.
        if self.state.scutum_landmarks_fiducial_node is not None:
            self.state.scutum_landmarks_fiducial_node.GetDisplayNode().SetVisibility(False)
        if self.state.scutum_threshold_seeds_fiducial_node is not None:
            self.state.scutum_threshold_seeds_fiducial_node.GetDisplayNode().SetVisibility(False)

        # Recenter the 3D view on the new model and orient the camera to
        # look from the correct side for this ear (self.state.ear_side) --
        # see base_page.WizardPage.recenter_3d_view() for why this can't
        # just always look from the Right.
        self.recenter_3d_view()

        self.ui.openSegmentEditorButton.setEnabled(True)
        # Explicit, unmissable-in-the-UI confirmation of whether sheet
        # enhancement actually ran on this click -- added after real-Slicer
        # testing showed no visible change across several tuning rounds,
        # so it was no longer safe to just assume the intended code path
        # was the one actually executing. See core/segment_threshold.py's
        # matching Python-console log line for the full diagnostic detail
        # (gamma_scale, per-response statistics, etc).
        sheetness_status = (
            f"Sheet enhancement: ON (gamma_scale={config.SHEETNESS_GAMMA_AUTO_SCALE})"
            if config.ENABLE_SHEET_ENHANCEMENT
            else "Sheet enhancement: OFF"
        )
        self.ui.statusLabel.setText(
            "Segmentation complete. Review it in the 3D view, adjust sliders "
            "and re-run if needed, or open Segment Editor for manual touch-ups.\n"
            f"[{sheetness_status}]"
        )

    def _on_open_segment_editor_clicked(self):
        import slicer

        # Mark the segmentation as possibly touched, so
        # _refresh_mesh_from_segmentation() knows to re-postprocess it --
        # see that method's comment for why this matters.
        self._segmentation_edited = True

        slicer.util.selectModule("SegmentEditor")
        # Hide the drawing model and show the segmentation in its place --
        # otherwise the two would overlap in the 3D view while editing.
        if self.state.scutum_bone_wall_model_node is not None:
            self.state.scutum_bone_wall_model_node.GetDisplayNode().SetVisibility(False)
        segmentation_display_node = self.state.scutum_bone_wall_segmentation_node.GetDisplayNode()
        segmentation_display_node.SetVisibility(True)
        segmentation_display_node.SetVisibility3D(True)
        editor_widget = slicer.modules.segmenteditor.widgetRepresentation().self().editor
        editor_widget.setSegmentationNode(self.state.scutum_bone_wall_segmentation_node)
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
        surgeon actually opened Segment Editor since this segmentation was
        last (re)built. Skipped entirely otherwise: see the matching
        comment in page_pinna_review.py's version of this method -- even
        without reapplying postprocessing, routing an *unedited*
        segmentation back through ExportVisibleSegmentsToLabelmapNode ->
        PullVolumeFromSlicer -> marching cubes isn't guaranteed to
        reproduce the exact mesh already built (and already reviewed) in
        _on_run_clicked(), and confirmed on a real scan (2026-07-27, pinna
        stage) that this round trip can erode real thin anatomy for no
        benefit when nothing actually changed. Same risk applies here in
        principle. If Segment Editor WAS used, the freshly hand-edited
        content hasn't been postprocessed at all yet, so re-deriving and
        postprocessing once here is still correct and necessary."""
        import slicer
        import sitkUtils

        segmentation_node = self.state.scutum_bone_wall_segmentation_node
        if segmentation_node is None:
            return True, ""

        temp_label_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "ScutumBoneWallEdited"
        )
        slicer.modules.segmentations.logic().ExportVisibleSegmentsToLabelmapNode(
            segmentation_node, temp_label_node
        )
        sitk_image = io_utils.flip_ras_lps(sitkUtils.PullVolumeFromSlicer(temp_label_node))
        slicer.mrmlScene.RemoveNode(temp_label_node)
        sitk_image = postprocess.run_full_postprocess(sitk_image, close_tunnels=True)
        self._check_and_display_wall_thickness(sitk_image)

        try:
            mesh = mesh_export.label_map_to_mesh(sitk_image)
        except mesh_export.EmptySegmentationError:
            return False, (
                "The segmentation is empty after your Segment Editor edits. "
                "Go back to Segment Editor and add material back, or "
                "re-run the automatic segmentation."
            )

        mesh_export.export_mesh(mesh, self.state.scutum_bone_wall_mesh_path)
        if self.state.scutum_bone_wall_model_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.scutum_bone_wall_model_node)
        self.state.scutum_bone_wall_model_node = slicer.util.loadModel(
            self.state.scutum_bone_wall_mesh_path
        )
        self.state.scutum_bone_wall_model_node.GetDisplayNode().SetVisibility(True)
        # Hide the segmentation again now that the refreshed model node is
        # showing the same thing -- keeps it from visually duplicating the
        # drawing model on the next page.
        segmentation_node.GetDisplayNode().SetVisibility(False)
        return True, ""

    def _on_reset_page_clicked(self):
        # clear_page_state already resets scutum_threshold_seeds/
        # scutum_threshold_seeds_fiducial_node to defaults (they're owned
        # by "scutum_review" -- see wizard_state.PAGE_OWNED_FIELDS) and
        # removes the seed fiducial node from the scene entirely. Re-run
        # the same setup on_enter() uses so a fresh node exists if the
        # surgeon places calibration points again -- self._seed_fiducial_node
        # would otherwise be left pointing at a now-deleted node.
        wizard_state.clear_page_state(self.state, "scutum_review")
        self._setup_calibration_seeds()
        self.ui.airThresholdSlider.value = config.DEFAULT_AIR_THRESHOLD
        self.ui.boneThresholdSlider.value = config.DEFAULT_BONE_THRESHOLD
        self.ui.openSegmentEditorButton.setEnabled(False)
        self.ui.wallThicknessWarningLabel.setText("")
        self.ui.wallThicknessWarningLabel.setVisible(False)
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
        # Only re-derive the mesh from the segmentation if Segment Editor
        # was actually used -- see _refresh_mesh_from_segmentation's
        # docstring for why doing this unconditionally is both pointless
        # and risky when nothing was actually edited.
        if getattr(self, "_segmentation_edited", False):
            return self._refresh_mesh_from_segmentation()
        return True, ""
