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
  - calibrationInstructionLabel (QLabel) -- current calibration step's instruction/status
  - placeCalibrationPointButton (QPushButton)
  - redoCalibrationButton       (QPushButton) -- clear both of this page's calibration points
  - skinThresholdSlider       (QSlider or ctkSliderWidget)
  - runButton                 (QPushButton)
  - openSegmentEditorButton  (QPushButton)
  - resetPageButton           (QPushButton) -- clear this page's own segmentation
  - revertToHereButton        (QPushButton) -- clear every later step, keep this segmentation
  - progressBar               (QProgressBar) -- shown only while a background step (see
    base_page.WizardPage.run_blocking()) is running, hidden otherwise
  - statusLabel               (QLabel)

Seed-click calibration (core/threshold_seeds.py, added 2026-07-31; made
REQUIRED, not optional, 2026-07-31 -- see on_leave_next()): the surgeon
clicks two points here (open air near the ear, then ordinary soft tissue
near the ear), which immediately pre-fills the Skin threshold slider
above with a per-scan-calibrated starting value
(core/threshold_seeds.calibrate_skin_threshold(), the midpoint of the
two) instead of the fixed config default. The slider itself remains
adjustable afterward -- the surgeon can still drag it to any value before
clicking Run -- but PLACING the two points is gated on Next, because the
soft-tissue point is also carried forward (state.pinna_soft_tissue_seed)
to help calibrate the scutum review page's bone threshold later,
alongside a bone point placed there -- one click, reused by both pages,
since it's correcting for this scan's overall HU calibration, not
sampling anatomy specific to either page. Confirmed on a real scan
(2026-07-31) that typing a threshold into the slider directly, instead of
placing these points, leaves pinna_soft_tissue_seed at None -- the scutum
page's "Auto-Calibrate & Segment" then silently falls back to
config.DEFAULT_BONE_THRESHOLD with no error, which is what this gate
exists to prevent. (A same-day first version of the calibration itself
tried to avoid the air click by guessing a fixed offset below just the
soft-tissue point -- reverted after its first real-Slicer test came out
badly wrong; see core/threshold_seeds.py's module docstring for the full
story.)
"""

from __future__ import annotations
import os
import time
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from EarReconstructionPlannerLib import wizard_state
from core import roi_crop, segment_pinna_threshold, postprocess, mesh_export, io_utils, threshold_seeds
import config


def _diag_component_count(sitk_image, label):
    """
    TEMPORARY (2026-08-22, remove once the mesh-fragmentation bug below is
    found) -- reports the label array's own connected-component count
    (voxel space, 6-connectivity, same convention as
    segment_pinna_threshold._label_6_connected) at a named pipeline stage.
    Added after a real-scan run showed the mesh coming OUT of
    label_map_to_mesh() was already non-watertight with 2 components --
    BEFORE decimate_to_target_resolution() ever touched it (it no-opped)
    -- on a scan with unusually anisotropic spacing (0.39/0.39/2.5mm, only
    32 slices). Bisects which upstream step (segment_pinna_region's own
    component selection + spike removal, or postprocess's
    remove_small_specks/fill_holes/smooth_boundary) is where a single
    selected component first becomes multiple.
    """
    import SimpleITK as sitk
    from scipy import ndimage
    import numpy as np

    array = sitk.GetArrayFromImage(sitk_image)
    structure = ndimage.generate_binary_structure(3, 1)  # 6-connectivity
    _, num_components = ndimage.label(array, structure=structure)
    print(f"[pinna diag] component count after {label}: {num_components} (spacing={sitk_image.GetSpacing()})")

# The 2-point calibration sequence placed on this page, in click order --
# same shape as the old core.threshold_seeds.SEED_STEPS pattern (removed
# 2026-07-31 when it was down to 1 point per page), but kept local here
# now that this page needs 2 points again -- see this file's module
# docstring and core/threshold_seeds.py's for why the air point came back.
_CALIBRATION_STEPS = [
    {
        "field": "pinna_air_seed",
        "instruction": "Required: click a point in open air near the ear (not touching skin).",
    },
    {
        "field": "pinna_soft_tissue_seed",
        "instruction": "Required: click a point on ordinary soft tissue near the ear.",
    },
]


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
            "Click 'Place Calibration Point' twice -- once in open air "
            "near the ear, once on ordinary soft tissue near the ear. "
            "Placing both points is required before you can continue past "
            "this page (manually typing a threshold into the slider "
            "doesn't give the scutum page's 'Auto-Calibrate' button the "
            "sample points it needs later) -- but the 'Skin threshold' "
            "slider itself stays fully yours to adjust: placing the "
            "points just pre-fills it with a value tuned to this specific "
            "scan, you can still drag it to anything you prefer afterward. "
            "If a point lands in the wrong spot, use 'Redo Calibration "
            "Points' to start over. The soft-tissue point also helps "
            "calibrate the bone-wall threshold later on the Temporal Bone "
            "Canal Segmentation page.\n\n"
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

        self._setup_calibration_seed()

        self.ui.placeCalibrationPointButton.clicked.connect(self._on_place_calibration_point_clicked)
        self.ui.redoCalibrationButton.clicked.connect(self._on_redo_calibration_clicked)
        self.ui.runButton.clicked.connect(self._on_run_clicked)
        self.ui.openSegmentEditorButton.clicked.connect(self._on_open_segment_editor_clicked)
        self.ui.resetPageButton.clicked.connect(self._on_reset_page_clicked)
        self.ui.revertToHereButton.clicked.connect(self._on_revert_to_here_clicked)
        self.ui.openSegmentEditorButton.setEnabled(self.state.pinna_region_mesh_path is not None)
        self.ui.progressBar.setVisible(False)
        self.ui.statusLabel.setText("Adjust the slider if needed, then click Run.")

    def _setup_calibration_seed(self):
        import slicer

        # Reuse the fiducial node from a previous visit if it's still live,
        # same reasoning as page_scutum_review.py's matching setup.
        existing = self.state.pinna_seed_fiducial_node
        if existing is not None and slicer.mrmlScene.IsNodePresent(existing):
            self._seed_fiducial_node = existing
        else:
            self._seed_fiducial_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode", "PinnaThresholdSeeds"
            )
            self._seed_fiducial_node.SetLocked(False)
            self._seed_fiducial_node.CreateDefaultDisplayNodes()
            self.state.pinna_seed_fiducial_node = self._seed_fiducial_node
        self._seed_fiducial_node.GetDisplayNode().SetVisibility(True)

        # Resume at whichever step matches what's already been placed --
        # same pattern page_scutum_review.py used to use for its old
        # 3-point sequence.
        self._current_calib_step = 0
        for step in _CALIBRATION_STEPS:
            if getattr(self.state, step["field"]) is None:
                break
            self._current_calib_step += 1

        self._calib_observer_tag = None
        self._update_calibration_step_display()

    def _update_calibration_step_display(self):
        if self._current_calib_step >= len(_CALIBRATION_STEPS):
            self.ui.calibrationInstructionLabel.setText(
                f"All {len(_CALIBRATION_STEPS)} calibration points placed. "
                "You can still adjust the Skin threshold slider below by "
                "hand before clicking Run."
            )
            self.ui.placeCalibrationPointButton.setEnabled(False)
        else:
            step = _CALIBRATION_STEPS[self._current_calib_step]
            self.ui.calibrationInstructionLabel.setText(step["instruction"])
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

        field_name = _CALIBRATION_STEPS[self._current_calib_step]["field"]
        setattr(self.state, field_name, tuple(ras))
        self._seed_fiducial_node.SetNthControlPointLabel(point_index, field_name)

        self._current_calib_step += 1
        self._update_calibration_step_display()

        if self.state.pinna_air_seed is not None and self.state.pinna_soft_tissue_seed is not None:
            self._calibrate_skin_threshold_from_seeds()

    def _calibrate_skin_threshold_from_seeds(self):
        """Pre-fills skinThresholdSlider once both calibration points are
        placed -- no separate "Auto-Calibrate" button, unlike
        page_scutum_review.py's embedded-Segment-Editor rework, since this
        page's Threshold step is still the older slider+Run pattern (see
        this file's module docstring). The slider stays the actual source
        of truth either way; this only changes its starting value."""
        import sitkUtils

        if self.state.volume_node is None:
            return

        sitk_image = io_utils.flip_ras_lps(sitkUtils.PullVolumeFromSlicer(self.state.volume_node))
        coarse_cropped = roi_crop.crop_to_point_region(
            sitk_image,
            self.state.pinna_landmarks.ear_center,
            radius_mm=config.PINNA_ROI_RADIUS_MM,
        )
        try:
            skin_threshold = threshold_seeds.calibrate_skin_threshold(
                coarse_cropped, self.state.pinna_air_seed, self.state.pinna_soft_tissue_seed
            )
        except ValueError as exc:
            self.ui.statusLabel.setText(str(exc))
            return

        self.ui.skinThresholdSlider.value = skin_threshold
        self.ui.statusLabel.setText(
            f"Slider pre-filled from your calibration points (~{skin_threshold:.0f} HU). "
            "Adjust if needed, then click Run."
        )

    def _on_redo_calibration_clicked(self):
        if self._seed_fiducial_node is not None:
            self._seed_fiducial_node.RemoveAllControlPoints()
        self.state.pinna_air_seed = None
        self.state.pinna_soft_tissue_seed = None
        self._current_calib_step = 0
        self._update_calibration_step_display()
        self.ui.skinThresholdSlider.value = config.SKIN_AIR_THRESHOLD
        self.ui.statusLabel.setText(
            "Calibration points cleared. Place them again to continue -- "
            "both are required before you can click Next."
        )

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

        # Fixes a real bug found on a badly-anisotropic scan (2026-08-22/25,
        # see CLAUDE.md "Pinna mesh decimation topology bug"): only
        # resampling the final mask right before marching_cubes wasn't
        # enough, because thresholding/connected-components/spike-removal/
        # postprocess below all still ran on the badly-conditioned native
        # grid first. Doing it here, as early as possible (before any of
        # those steps), is what actually matched Thomas's own manual fix
        # of resampling the whole scan to isotropic before running the
        # pipeline. No-ops on an already-near-isotropic scan -- see
        # io_utils.resample_to_bounded_anisotropy's docstring.
        _t0 = time.time()
        coarse_cropped = io_utils.resample_to_bounded_anisotropy(
            coarse_cropped, config.MESH_MAX_ANISOTROPY_RATIO
        )
        print(f"[pinna timing] resample_to_bounded_anisotropy: {time.time() - _t0:.2f}s (size {coarse_cropped.GetSize()})")

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
        _diag_component_count(region_mask, "segment_pinna_region (incl. spike removal)")

        _t0 = time.time()
        region_mask = self.run_blocking(
            lambda: postprocess.run_full_postprocess(region_mask),
            status_text="Cleaning up segmentation...",
        )
        self.ui.progressBar.setValue(4)
        print(f"[pinna timing] postprocess TOTAL: {time.time() - _t0:.2f}s")
        _diag_component_count(region_mask, "postprocess.run_full_postprocess")

        _t0 = time.time()
        region_mask = self.run_blocking(
            lambda: roi_crop.crop_to_own_bounding_box(region_mask, config.PINNA_TIGHT_CROP_MARGIN_MM),
            status_text="Cropping to tight bounding box...",
        )
        self.ui.progressBar.setValue(5)
        print(f"[pinna timing] crop_to_own_bounding_box: {time.time() - _t0:.2f}s (size {region_mask.GetSize()})")
        print(f"[pinna timing] Stage A grand total: {time.time() - _t_total:.2f}s")
        _diag_component_count(region_mask, "crop_to_own_bounding_box")

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
            lambda: mesh_export.label_map_to_mesh(
                region_mask, max_anisotropy_ratio=config.MESH_MAX_ANISOTROPY_RATIO
            ),
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
        if self.state.pinna_seed_fiducial_node is not None:
            self.state.pinna_seed_fiducial_node.GetDisplayNode().SetVisibility(False)

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

        # Same badly-anisotropic-scan fix as _run_segmentation_pipeline()
        # above, applied here too since this path re-derives from a
        # hand-edited segmentation rather than going through thresholding
        # -- postprocess's own voxel-radius-based morphology below needs
        # the better-conditioned grid just as much. No-ops on an
        # already-near-isotropic scan.
        sitk_image = io_utils.resample_to_bounded_anisotropy(
            sitk_image, config.MESH_MAX_ANISOTROPY_RATIO, is_label=True
        )

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
                    lambda: mesh_export.label_map_to_mesh(
                        sitk_image, max_anisotropy_ratio=config.MESH_MAX_ANISOTROPY_RATIO
                    ),
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
        # clear_page_state also resets pinna_air_seed/pinna_soft_tissue_seed/
        # pinna_seed_fiducial_node (owned by "pinna_review" -- see
        # wizard_state.PAGE_OWNED_FIELDS) and removes their MRML node from
        # the scene -- re-run the calibration setup so a fresh fiducial
        # node exists and the label/button reflect the clear.
        wizard_state.clear_page_state(self.state, "pinna_review")
        self._setup_calibration_seed()
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
        # Required, not just a convenience -- confirmed 2026-07-31 that
        # skipping this (e.g. typing a threshold into the slider directly)
        # leaves state.pinna_soft_tissue_seed at None, which silently makes
        # the scutum review page's "Auto-Calibrate & Segment" button fall
        # back to config.DEFAULT_BONE_THRESHOLD instead of using the
        # surgeon's own scan-specific calibration, with no error -- just a
        # quietly wrong-looking result two pages later. Gating here catches
        # it at the source instead.
        if self.state.pinna_air_seed is None or self.state.pinna_soft_tissue_seed is None:
            return False, (
                "Please place both calibration points above (open air, "
                "then soft tissue) before continuing -- click 'Place "
                "Calibration Point'. Once placed, you can still adjust "
                "the Skin threshold slider by hand if you want a "
                "different value."
            )
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
