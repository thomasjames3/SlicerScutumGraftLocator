"""
page_scutum_review.py
========================
Page 6: segment the bone wall using Slicer's own, surgeon-familiar
Segment Editor -- embedded directly in this page (no navigating away to
a separate module) -- and let the surgeon review/adjust it live before
moving on.

Interactive Segment Editor rework (2026-07-30): previously this page ran
its own custom SimpleITK threshold pipeline (core/segment_threshold.py's
shell-restricted air-lumen-then-bone-wall search, with optional Hessian
sheet enhancement) behind two plain sliders, with a separate "Open Segment
Editor" button that navigated away to Slicer's SegmentEditor module for
manual paint/erase touch-up. Thomas found Slicer's own interactive
Threshold effect -- live preview as you drag, directly against the real
data -- consistently more accurate than the automated pipeline could
manage on its own (see CLAUDE.md's "Canal segmentation improvements" for
the repeated real-scan struggles that pipeline had). This page now embeds
a real `qMRMLSegmentEditorWidget` (Threshold + Paint/Erase/Islands/
Smoothing effects) directly here:

  1. "Auto-Calibrate & Segment" (one click) computes a starting bone
     threshold -- from the optional 3-point seed calibration if placed,
     else config.DEFAULT_BONE_THRESHOLD -- and applies it via the
     Threshold effect to the WHOLE loaded volume (not a shell/ROI
     restriction; that came later, see below), giving an immediate live
     segmentation. Slicer auto-generates a real-time 3D closed-surface
     preview for a visible segmentation node, so the surgeon sees the
     result in the 3D view immediately, no custom mesh pipeline needed
     yet.
  2. The embedded widget stays live and interactive: dragging the
     Threshold effect's own Minimum/Maximum sliders re-previews instantly
     against the real volume, and Paint/Erase/Islands/Smoothing are
     available for manual touch-up -- all inline, no page navigation.
  3. "Preview 3D Result" (or Next) finalizes: pulls the segmentation's
     current content, crops it down to the precise cylinder ROI (see
     core/roi_crop.py), keeps only the connected component nearest the
     canal axis (core/segment_threshold.py's axis-distance trick, reused
     here since whole-volume thresholding can otherwise pick up unrelated
     bone elsewhere in the ROI -- e.g. ossicles, or a sliver of adjacent
     skull), runs the existing postprocess (speck removal/hole fill/
     tunnel closing), and meshes the result for the draw page.

Not used anymore by this page (left in place, still valid as a possible
future Stage B fallback path via segment_dl.py): the old
segment_threshold.segment_bone_wall()/segment_dl.segment() call, its air/
bone slider pair, and the Hessian sheet-enhancement feature. Sub-voxel
mesh extraction (mesh_export.label_map_to_mesh_subvoxel) is also no
longer used here -- it only makes sense when a mask comes from a single,
known threshold value with nothing else touching it, which no longer
holds once Paint/Erase/manual edits can sit on top of the initial
threshold. Plain label_map_to_mesh() is used instead, same as this page
already did for a hand-edited segmentation before this rework.

Expected widgets in page_scutum_review.ui:
  - tutorialLabel        (QLabel) -- extra guidance, shown only in tutorial mode
  - calibrationStepProgressLabel (QLabel) -- e.g. "Optional calibration: 0 of 3"
  - calibrationInstructionLabel  (QLabel) -- current calibration step's instruction
  - placeCalibrationPointButton  (QPushButton)
  - redoCalibrationButton        (QPushButton) -- clear just the 3 calibration points
  - calibrateButton      (QPushButton) -- one-click auto-threshold + apply
  - segmentEditorPlaceholder (QWidget, native, with its own layout) -- the
    embedded qMRMLSegmentEditorWidget is inserted into this at runtime
  - finalizeButton        (QPushButton) -- "Preview 3D Result": crop/select/
    postprocess/mesh the current segmentation
  - progressBar          (QProgressBar) -- shown only while a background step (see
    base_page.WizardPage.run_blocking()) is running, hidden otherwise
  - statusLabel          (QLabel)
  - wallThicknessWarningLabel (QLabel) -- advisory-only, shown if the segmented wall looks too thin to trust
  - resetPageButton      (QPushButton) -- clear this page's own segmentation
  - revertToHereButton   (QPushButton) -- clear every later step, keep this segmentation

Optional seed-click calibration (core/threshold_seeds.py): the surgeon can
click 3 points (air / bone / soft tissue) before running, which feeds
"Auto-Calibrate & Segment" a per-scan-calibrated starting bone threshold
instead of the fixed config default. Purely a convenience -- the embedded
Threshold effect's own sliders remain the actual source of truth and stay
manually adjustable either way.

Post-segmentation thin-wall check (core/wall_quality.py): whenever the
segmentation is finalized, the resulting wall is checked for suspiciously-
thin regions and flagged via wallThicknessWarningLabel if found. Advisory
only -- never blocks Next. See config.py's "post-segmentation
wall-thickness warning" section for why no threshold choice can fix this
failure mode.
"""

from __future__ import annotations
import os
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from EarReconstructionPlannerLib import wizard_state
from core import roi_crop, postprocess, mesh_export, io_utils, threshold_seeds, wall_quality, segment_threshold
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
        self._setup_segment_editor()

        self.set_tutorial_text(
            "Before running, you can optionally click 'Place Calibration "
            "Point' three times -- once inside the air-filled canal, once "
            "on bone, once on soft tissue -- so 'Auto-Calibrate & Segment' "
            "starts from a threshold tuned to this specific scan. This step "
            "is optional; the plain default works reasonably well without "
            "it. If a point lands in the wrong spot, use 'Redo Calibration "
            "Points' to start over.\n\n"
            "Click 'Auto-Calibrate & Segment' to get a first-pass "
            "segmentation immediately. Below that is Slicer's own "
            "segmentation tool, embedded right here: drag the Threshold "
            "effect's Minimum/Maximum sliders and you'll see the result "
            "update live, directly on the real scan. If small gaps or "
            "stray bits remain, switch to the Paint, Erase, Islands, or "
            "Smoothing effect (the row of buttons above the sliders) to "
            "touch it up by hand -- click or drag over the volume in a "
            "slice view.\n\n"
            "Look at the result in the 3D view as you go (left-drag to "
            "rotate, middle-drag to pan, scroll or right-drag to zoom). The "
            "3D view has its own small toolbar in the top-left corner: the "
            "recenter button (a crosshair/target icon) reframes the camera "
            "around whatever's currently visible. The small R/L/A/P/S/I "
            "axis-letter widget in the corner lets you click a letter to "
            "snap the camera to look from exactly that direction.\n\n"
            "When it looks right, click 'Preview 3D Result' to build the "
            "smoothed, cropped 3D surface the next page will use for "
            "drawing -- or just click Next, which does the same thing "
            "automatically.\n\n"
            "If a message appears after previewing saying the wall looks "
            "very thin in places, it means the scan's resolution may not "
            "be fine enough to reliably show the true wall thickness "
            "there. You can still continue, but treat that area with "
            "extra caution and consider a manual double-check."
        )

        self.ui.calibrateButton.clicked.connect(self._on_calibrate_clicked)
        self.ui.finalizeButton.clicked.connect(self._on_finalize_clicked)
        self.ui.resetPageButton.clicked.connect(self._on_reset_page_clicked)
        self.ui.revertToHereButton.clicked.connect(self._on_revert_to_here_clicked)
        self.ui.placeCalibrationPointButton.clicked.connect(self._on_place_calibration_point_clicked)
        self.ui.redoCalibrationButton.clicked.connect(self._on_redo_calibration_clicked)
        self.ui.wallThicknessWarningLabel.setText("")
        self.ui.wallThicknessWarningLabel.setVisible(False)
        self.ui.progressBar.setVisible(False)
        self.ui.statusLabel.setText(
            "Click 'Auto-Calibrate & Segment' to get started, or adjust the "
            "Threshold sliders below directly."
        )

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
            self.ui.statusLabel.setText(
                "All calibration points placed. Click 'Auto-Calibrate & "
                "Segment' to use them."
            )

    def _on_redo_calibration_clicked(self):
        if self._seed_fiducial_node is not None:
            self._seed_fiducial_node.RemoveAllControlPoints()
        self.state.scutum_threshold_seeds = ThresholdSeeds()
        self._current_calib_step = 0
        self._update_calibration_step_display()
        self.ui.statusLabel.setText(
            "Calibration points cleared. Place them again, or use "
            "'Auto-Calibrate & Segment' / the Threshold sliders directly."
        )

    def _setup_segment_editor(self):
        """Creates (or reuses) the persistent bone-wall segmentation node
        and embeds a real qMRMLSegmentEditorWidget directly into this
        page's layout -- the surgeon edits it inline, live, rather than
        navigating away to Slicer's separate SegmentEditor module. The
        widget instance itself is created once and cached on `self`
        (this page controller is itself cached across visits -- see
        EarReconstructionPlanner.py's _get_or_create_controller()), so
        re-entering this page just re-points it at the current
        segmentation node rather than rebuilding it."""
        import slicer
        import qt

        existing = self.state.scutum_bone_wall_segmentation_node
        if existing is not None and slicer.mrmlScene.IsNodePresent(existing):
            segmentation_node = existing
        else:
            segmentation_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLSegmentationNode", "ScutumBoneWallSegmentation"
            )
            segmentation_node.CreateDefaultDisplayNodes()
            self.state.scutum_bone_wall_segmentation_node = segmentation_node
        if segmentation_node.GetSegmentation().GetNumberOfSegments() == 0:
            segmentation_node.GetSegmentation().AddEmptySegment("BoneWall")

        # Show the live segmentation (not the finalized drawable model, if
        # one already exists from a previous visit) while this page is
        # active -- Slicer auto-generates a real-time 3D closed-surface
        # representation for a visible segmentation node, which is what
        # gives the "live visualization while adjusting" experience this
        # rework is built around.
        segmentation_node.GetDisplayNode().SetVisibility(True)
        segmentation_node.GetDisplayNode().SetVisibility3D(True)
        if self.state.scutum_bone_wall_model_node is not None:
            self.state.scutum_bone_wall_model_node.GetDisplayNode().SetVisibility(False)

        # Track segmentation content changes so on_leave_next() knows
        # whether a fresh finalize is actually needed -- see that method's
        # comment for why this replaces the old "_segmentation_edited"
        # flag (which only tracked a separate "Open Segment Editor"
        # excursion; editing is now the primary in-page interaction, so it
        # needs to be tracked automatically instead).
        if getattr(self, "_observed_segmentation_node", None) is not segmentation_node:
            segmentation_node.AddObserver("ModifiedEvent", self._on_segmentation_modified)
            self._observed_segmentation_node = segmentation_node
            self._needs_finalize = True

        if getattr(self, "_segment_editor_widget", None) is None:
            self._segment_editor_widget = slicer.qMRMLSegmentEditorWidget()
            self._segment_editor_widget.setMRMLScene(slicer.mrmlScene)
            editor_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentEditorNode")
            self._segment_editor_widget.setMRMLSegmentEditorNode(editor_node)
            # Curated effect list -- the full Segment Editor exposes far
            # more effects than a surgeon needs here. Threshold is the
            # primary tool this whole rework is built around; Paint/Erase/
            # Islands/Smoothing cover manual touch-up without overwhelming
            # a non-Python-savvy surgeon with the rest (matches this
            # project's "sliders not raw thresholds" usability priority).
            self._segment_editor_widget.setEffectNameOrder(
                ["Threshold", "Paint", "Erase", "Islands", "Smoothing"]
            )
            self._segment_editor_widget.unorderedEffectsVisible = False

            layout = self.ui.segmentEditorPlaceholder.layout()
            if layout is None:
                layout = qt.QVBoxLayout(self.ui.segmentEditorPlaceholder)
            layout.addWidget(self._segment_editor_widget)

        self._segment_editor_widget.setSegmentationNode(segmentation_node)
        # Slicer 5.2+ renamed "master volume" to "source volume" -- support
        # both since this hasn't been runtime-tested against a specific
        # Slicer version.
        if hasattr(self._segment_editor_widget, "setSourceVolumeNode"):
            self._segment_editor_widget.setSourceVolumeNode(self.state.volume_node)
        else:
            self._segment_editor_widget.setMasterVolumeNode(self.state.volume_node)

    def _on_segmentation_modified(self, caller, event):
        self._needs_finalize = True

    def _on_calibrate_clicked(self):
        import sitkUtils
        import slicer

        if self.state.volume_node is None:
            self.ui.statusLabel.setText("No scan loaded -- go back and select one first.")
            return
        if not self.state.scutum_landmarks.is_complete():
            self.ui.statusLabel.setText(
                "Ear canal landmarks aren't placed yet -- go back and place them first."
            )
            return

        self.ui.statusLabel.setText("Calibrating threshold...")
        slicer.app.processEvents()

        # Only used here to sample intensities for calibration -- the
        # Threshold effect itself operates directly on self.state.volume_node
        # (the whole loaded scan), not this pulled/cropped copy. See this
        # file's module docstring for why thresholding no longer needs its
        # own ROI restriction up front (that happens afterward, in
        # _finalize_pipeline).
        sitk_image = io_utils.flip_ras_lps(sitkUtils.PullVolumeFromSlicer(self.state.volume_node))
        coarse_cropped = roi_crop.crop_to_landmark_region(sitk_image, self.state.scutum_landmarks)

        if self.state.scutum_threshold_seeds.is_complete():
            try:
                _, bone_threshold = threshold_seeds.calibrate_thresholds(
                    coarse_cropped, self.state.scutum_threshold_seeds
                )
            except ValueError as exc:
                self.ui.statusLabel.setText(str(exc))
                return
            source = "your calibration points"
        else:
            bone_threshold = config.DEFAULT_BONE_THRESHOLD
            source = "the default starting value"

        self._apply_threshold(bone_threshold, config.SCUTUM_INTERACTIVE_THRESHOLD_MAX_HU)

        self.ui.statusLabel.setText(
            f"Segmented using {source} (bone threshold ~{bone_threshold:.0f} HU). "
            "Drag the Threshold sliders below to adjust live, or use Paint/"
            "Erase/Islands/Smoothing for manual touch-up. Click 'Preview 3D "
            "Result' to see the final drawable surface, or just click Next "
            "when it looks right."
        )

    def _apply_threshold(self, min_value: float, max_value: float) -> None:
        """Drives Slicer's built-in Threshold effect programmatically,
        following the documented Slicer script-repository recipe
        (effect.setParameter("MinimumThreshold"/"MaximumThreshold", ...)
        then effect.self().onApply()) -- unconfirmed against this
        project's real Slicer install, like every other Segment-Editor-API
        assumption noted in CLAUDE.md's Known Issues."""
        self._segment_editor_widget.setActiveEffectByName("Threshold")
        effect = self._segment_editor_widget.activeEffect()
        if effect is None:
            return
        effect.setParameter("MinimumThreshold", str(min_value))
        effect.setParameter("MaximumThreshold", str(max_value))
        effect.self().onApply()

    def _check_and_display_wall_thickness(self, bone_wall_sitk_image):
        warning = wall_quality.check_wall_thickness(bone_wall_sitk_image)
        self.ui.wallThicknessWarningLabel.setText(warning or "")
        self.ui.wallThicknessWarningLabel.setVisible(bool(warning))

    def _on_finalize_clicked(self):
        import sitkUtils
        import slicer

        self.ui.finalizeButton.setEnabled(False)
        self.ui.progressBar.setVisible(True)
        self.ui.progressBar.setMinimum(0)
        self.ui.progressBar.setMaximum(6)
        self.ui.progressBar.setValue(0)
        try:
            self._finalize_pipeline(sitkUtils, slicer)
        finally:
            self.ui.finalizeButton.setEnabled(True)
            self.ui.progressBar.setVisible(False)

    def _run_finalize(self):
        """Shared by the 'Preview 3D Result' button and on_leave_next() --
        see _finalize_pipeline() for the actual steps. Returns (bool, str)
        matching the WizardPage.on_leave_next() contract."""
        import sitkUtils
        import slicer

        self.ui.progressBar.setVisible(True)
        self.ui.progressBar.setMinimum(0)
        self.ui.progressBar.setMaximum(6)
        self.ui.progressBar.setValue(0)
        try:
            return self._finalize_pipeline(sitkUtils, slicer)
        finally:
            self.ui.progressBar.setVisible(False)

    def _finalize_pipeline(self, sitkUtils, slicer):
        """Derives the final drawable mesh from whatever is currently in
        the live segmentation node -- crop to the precise ROI, keep only
        the component nearest the canal axis, postprocess, mesh. Always
        re-derives fresh from the segmentation node's current content
        (never re-processes an already-finalized mesh), so this stays safe
        against the "unconditional reprocessing erodes an already-good
        result" failure mode documented in CLAUDE.md's Isolate Patch saga
        -- the segmentation node itself, not a prior mesh, is the single
        source of truth here.

        Known tradeoff: keeping only the single connected component
        nearest the canal axis means a manually-painted addition that
        isn't connected to the main wall gets discarded. This matches the
        existing shell-restriction philosophy elsewhere in this pipeline
        (prevents accidentally keeping unrelated bone, e.g. ossicles or a
        sliver of adjacent skull, now that thresholding runs across the
        whole volume instead of a restricted shell) -- if a surgeon needs
        to keep a deliberately-separate painted region, the Islands effect
        (in the curated effect list above) is the tool for merging/
        managing that before clicking Preview/Next.
        """
        wizard_state.clear_downstream_state(self.state, "scutum_review")

        self.ui.wallThicknessWarningLabel.setText("")
        self.ui.wallThicknessWarningLabel.setVisible(False)

        segmentation_node = self.state.scutum_bone_wall_segmentation_node
        if segmentation_node is None or segmentation_node.GetSegmentation().GetNumberOfSegments() == 0:
            message = "Nothing segmented yet -- click 'Auto-Calibrate & Segment' first."
            self.ui.statusLabel.setText(message)
            return False, message

        self.ui.statusLabel.setText("Reading current segmentation...")
        slicer.app.processEvents()

        # ExportVisibleSegmentsToLabelmapNode/PullVolumeFromSlicer is a
        # plain image-format conversion, not the per-voxel physical-
        # coordinate math that caused the real memory blowup documented in
        # roi_crop.py -- fine to do once at full resolution. The coarse
        # crop right after keeps everything downstream small, same as
        # every other pipeline in this project.
        temp_label_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "ScutumBoneWallExport"
        )
        slicer.modules.segmentations.logic().ExportVisibleSegmentsToLabelmapNode(
            segmentation_node, temp_label_node, self.state.volume_node
        )
        full_label = io_utils.flip_ras_lps(sitkUtils.PullVolumeFromSlicer(temp_label_node))
        slicer.mrmlScene.RemoveNode(temp_label_node)

        full_image = io_utils.flip_ras_lps(sitkUtils.PullVolumeFromSlicer(self.state.volume_node))

        coarse_label = roi_crop.crop_to_landmark_region(full_label, self.state.scutum_landmarks)
        coarse_image = roi_crop.crop_to_landmark_region(full_image, self.state.scutum_landmarks)
        self.ui.progressBar.setValue(1)

        self.ui.statusLabel.setText("Building region of interest...")
        try:
            roi_mask = self.run_blocking(
                lambda: roi_crop.build_roi_mask(coarse_image, self.state.scutum_landmarks)
            )
        except ValueError as exc:
            self.ui.statusLabel.setText(str(exc))
            return False, str(exc)
        self.ui.progressBar.setValue(2)

        def _crop_to_roi_bbox():
            return (
                roi_crop.crop_to_roi_bounding_box(coarse_image, roi_mask),
                roi_crop.crop_to_roi_bounding_box(coarse_label, roi_mask),
                roi_crop.crop_to_roi_bounding_box(roi_mask, roi_mask),
            )

        cropped_image, cropped_label, cropped_roi_mask = self.run_blocking(
            _crop_to_roi_bbox, status_text="Cropping to region of interest..."
        )
        self.ui.progressBar.setValue(3)

        def _select_component():
            import numpy as np
            import SimpleITK as sitk

            label_array = sitk.GetArrayFromImage(cropped_label).astype(bool)
            roi_array = sitk.GetArrayFromImage(cropped_roi_mask).astype(bool)
            restricted = label_array & roi_array

            if not restricted.any():
                return None

            # Whole-volume thresholding can pick up unrelated bone
            # elsewhere in the ROI -- keep only the connected piece
            # closest to the surgeon's canal axis, same trick
            # segment_threshold.py uses for the air lumen (the
            # implementation is generic; reused here for the bone-labeled
            # array instead).
            labeled_array, num_components = segment_threshold._label_6_connected(restricted)
            if num_components == 0:
                return None
            best_id = segment_threshold._closest_component_to_axis_line(
                labeled_array, num_components, cropped_image, self.state.scutum_landmarks
            )
            selected_array = (labeled_array == best_id).astype(np.uint8)
            selected = sitk.GetImageFromArray(selected_array)
            selected.CopyInformation(cropped_image)
            return selected

        raw_bone_wall = self.run_blocking(_select_component, status_text="Selecting the canal wall...")
        self.ui.progressBar.setValue(4)

        if raw_bone_wall is None:
            message = (
                "No material found inside the region of interest. Go back to "
                "the Threshold effect and lower the minimum, or paint some "
                "material in directly."
            )
            self.ui.statusLabel.setText(message)
            return False, message

        bone_wall = self.run_blocking(
            lambda: postprocess.run_full_postprocess(raw_bone_wall, close_tunnels=True),
            status_text="Cleaning up segmentation...",
        )
        self.ui.progressBar.setValue(5)
        self._check_and_display_wall_thickness(bone_wall)

        # Plain marching cubes, not the sub-voxel variant -- this
        # segmentation may include manual Paint/Erase touch-ups on top of
        # the threshold, so there's no longer a single isovalue the whole
        # boundary corresponds to (see this file's module docstring).
        try:
            mesh = self.run_blocking(
                lambda: mesh_export.label_map_to_mesh(bone_wall),
                status_text="Building 3D surface mesh...",
            )
        except mesh_export.EmptySegmentationError:
            message = (
                "No bone wall found after cleanup. Try lowering the threshold "
                "minimum or painting in more material, or go back and "
                "double-check the landmark placement."
            )
            self.ui.statusLabel.setText(message)
            return False, message
        self.ui.progressBar.setValue(6)

        mesh_path = os.path.join(
            self.state.working_dir or slicer.app.temporaryPath, "scutum_bone_wall.stl"
        )
        mesh_export.export_mesh(mesh, mesh_path)
        self.state.scutum_bone_wall_mesh_path = mesh_path

        if self.state.scutum_bone_wall_model_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.scutum_bone_wall_model_node)
        self.state.scutum_bone_wall_model_node = slicer.util.loadModel(mesh_path)

        # Hide the live segmentation now that the finalized drawable model
        # is showing the same thing -- avoids visually duplicating it in
        # the 3D view.
        segmentation_node.GetDisplayNode().SetVisibility(False)

        if self.state.scutum_landmarks_fiducial_node is not None:
            self.state.scutum_landmarks_fiducial_node.GetDisplayNode().SetVisibility(False)
        if self.state.scutum_threshold_seeds_fiducial_node is not None:
            self.state.scutum_threshold_seeds_fiducial_node.GetDisplayNode().SetVisibility(False)

        self.recenter_3d_view()

        self._needs_finalize = False
        self.ui.statusLabel.setText(
            "3D surface ready. Review it in the 3D view -- go back to the "
            "Threshold/Paint/Erase tools above and click 'Preview 3D Result' "
            "again if you want to adjust it further, or click Next to continue."
        )
        return True, ""

    def _on_reset_page_clicked(self):
        # clear_page_state already resets scutum_threshold_seeds/
        # scutum_threshold_seeds_fiducial_node/scutum_bone_wall_segmentation_node
        # to defaults (they're owned by "scutum_review" -- see
        # wizard_state.PAGE_OWNED_FIELDS) and removes their MRML nodes from
        # the scene entirely. Re-run the same setup on_enter() uses so a
        # fresh segmentation node/fiducial node exists for the surgeon to
        # use again.
        wizard_state.clear_page_state(self.state, "scutum_review")
        self._observed_segmentation_node = None
        self._needs_finalize = True
        self._setup_calibration_seeds()
        self._setup_segment_editor()
        self.ui.wallThicknessWarningLabel.setText("")
        self.ui.wallThicknessWarningLabel.setVisible(False)
        self.ui.statusLabel.setText(
            "Segmentation cleared. Click 'Auto-Calibrate & Segment' to start again."
        )

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

    def on_leave_back(self):
        self._deactivate_segment_editor()

    def on_leave_next(self):
        self._deactivate_segment_editor()
        # Skip re-finalizing if nothing has changed in the segmentation
        # since the last successful finalize -- see _setup_segment_editor's
        # ModifiedEvent observer. Always finalizes at least once (a fresh
        # controller/page has no mesh path yet), and always re-finalizes
        # after any Threshold/Paint/Erase/Islands/Smoothing change.
        if self.state.scutum_bone_wall_mesh_path is not None and not getattr(self, "_needs_finalize", True):
            return True, ""
        ok, message = self._run_finalize()
        if not ok:
            return False, message or "Please finish segmenting before continuing."
        return True, ""

    def _deactivate_segment_editor(self):
        """Standard Slicer module-exit convention for an embedded Segment
        Editor widget -- deactivating the active effect releases any
        interaction-event observers it holds on the slice/3D views, so
        they don't linger while a different wizard page is showing."""
        widget = getattr(self, "_segment_editor_widget", None)
        if widget is not None:
            widget.setActiveEffectByName(None)
