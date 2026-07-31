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
     OWN closed-surface representation directly -- see "Mesh-based
     finalize rework" below -- crops it down to the precise cylinder ROI
     in mesh space (core/roi_crop.points_inside_roi(),
     core/mesh_export.crop_mesh_to_vertex_mask()), keeps only the
     connected component nearest the canal axis
     (core/mesh_export.select_mesh_component_nearest_axis(), the
     mesh-space equivalent of core/segment_threshold.py's axis-distance
     trick -- needed since whole-volume thresholding can otherwise pick
     up unrelated bone elsewhere in the ROI, e.g. ossicles or a sliver of
     adjacent skull), and exports the result for the draw page.

Mesh-based finalize rework (2026-07-31): the ORIGINAL version of this
step instead re-exported the segmentation to a labelmap and ran a
completely separate pipeline against it -- crop, ROI-mask intersect,
core/segment_threshold.py's voxel-array component selection,
postprocess.run_full_postprocess() (speck removal/hole fill/tunnel
closing), then core/mesh_export.label_map_to_mesh() (skimage
marching_cubes + a hand-tuned mask blur, see CLAUDE.md's "Scutum finalize
mesh quality" for 3 rounds of real-scan tuning attempts). Root cause
finally identified: exporting the segmentation to a labelmap was never
lossy on its own (Segment Editor's Threshold/Paint/Erase already edit a
binary labelmap directly, at state.volume_node's own resolution -- no
oversampling is configured here) -- the real mismatch was that the LIVE
3D view during editing is Slicer's own auto-generated closed surface
(vtkDiscreteFlyingEdges3D + vtkWindowedSincPolyDataFilter at Slicer's
default smoothing factor 0.5), while the old finalize step re-derived an
entirely different mesh via our own marching_cubes + smoothing -- two
different algorithms on similar-but-not-identical voxel data, which is
why the finalized mesh never quite matched what looked good live no
matter how the smoothing/blur constants were tuned.

Fix: finalize now pulls Slicer's OWN closed-surface representation
directly (_export_segment_as_trimesh(), via
ExportVisibleSegmentsToModels -- confirmed via Slicer's script-repository
docs to return model nodes in world/RAS coordinates) instead of
re-deriving a mesh from a fresh labelmap. This guarantees the finalized
mesh is exactly what was shown live, by construction -- no separate
smoothing pipeline to keep in sync with Slicer's own.

EXPERIMENTAL, Thomas's explicit call (2026-07-31): this also means
postprocess.run_full_postprocess() no longer runs AT ALL for this page --
no speck removal, no hole filling, and critically no tunnel_closing
(config.py notes this region is "genuinely tube-shaped", i.e. prone to
real topological handles). Deliberately accepted for now, to be tested:
the draw page (page_scutum_draw.py) feeds this exact mesh into
core/mesh_isolate.isolate_surface_patch(), the same flood-fill engine
that took an 8-round saga to make robust against exactly this kind of
topological handle (see CLAUDE.md's "Isolate Patch / mesh topology
saga"). Thomas's reasoning: this mesh only needs to accurately depict the
scutum, not be a polished/watertight display asset, so the cleanup steps
may not be worth their cost -- but this hasn't been tested against
Isolate Patch yet. **Test Isolate Patch specifically on the scutum draw
page after finalizing here, before trusting this path** -- if it breaks,
the fallback is porting close_small_tunnels()-equivalent cleanup to mesh
space (e.g. via trimesh's own hole-filling), not reverting to the old
labelmap-based finalize (which solved the topology risk but reintroduced
the mesh-quality mismatch this rework exists to fix).

Known, deliberately-accepted gap from the same change: the thin-wall
advisory check (wall_quality.check_wall_thickness(), needs a labelmap)
isn't wired up for this mesh-only path yet -- wallThicknessWarningLabel
stays empty/hidden on finalize until/unless this path is kept and that
check is ported to work on a mesh instead.

Not used anymore by this page (left in place, still valid elsewhere):
core/segment_threshold.py's segment_bone_wall()/air-bone slider pair/
Hessian sheet-enhancement (possible future Stage B fallback via
segment_dl.py), and -- as of this rework -- also its
_closest_component_to_axis_line()/_label_6_connected() (superseded by
the mesh-space equivalents above) and postprocess.run_full_postprocess()
(see EXPERIMENTAL note above). Sub-voxel mesh extraction
(mesh_export.label_map_to_mesh_subvoxel) and plain label_map_to_mesh()
are also no longer used here -- both only make sense starting from a
labelmap, which this page's finalize step no longer produces.

Expected widgets in page_scutum_review.ui:
  - tutorialLabel        (QLabel) -- extra guidance, shown only in tutorial mode
  - calibrationInstructionLabel  (QLabel) -- current calibration point's instruction/status
  - placeCalibrationPointButton  (QPushButton)
  - redoCalibrationButton        (QPushButton) -- clear this page's own calibration point
  - calibrateButton      (QPushButton) -- one-click auto-threshold + apply
  - segmentEditorPlaceholder (QWidget, native, with its own layout) -- the
    embedded qMRMLSegmentEditorWidget is inserted into this at runtime
  - finalizeButton        (QPushButton) -- "Preview 3D Result": exports the
    current segmentation's own closed-surface mesh, crops/selects in mesh
    space (see "Mesh-based finalize rework" above)
  - progressBar          (QProgressBar) -- shown only while a background step (see
    base_page.WizardPage.run_blocking()) is running, hidden otherwise
  - statusLabel          (QLabel)
  - wallThicknessWarningLabel (QLabel) -- advisory-only, shown if the segmented wall looks too thin to trust
  - resetPageButton      (QPushButton) -- clear this page's own segmentation
  - revertToHereButton   (QPushButton) -- clear every later step, keep this segmentation

Optional seed-click calibration (core/threshold_seeds.py): the surgeon can
click one point here (on solid bone near the ear canal), which combines
with a soft-tissue point placed earlier on the pinna review page
(state.pinna_soft_tissue_seed) to feed "Auto-Calibrate & Segment" a
per-scan-calibrated starting bone threshold instead of the fixed config
default. Purely a convenience -- the embedded Threshold effect's own
sliders remain the actual source of truth and stay manually adjustable
either way. (A third point, inside the air-filled canal, used to also be
placed here to calibrate a now-unused air_threshold -- removed 2026-07-31
once this page's own Segment-Editor rework made that value dead weight;
see core/threshold_seeds.py's module docstring.)

Post-segmentation thin-wall check (core/wall_quality.py): advisory-only,
never blocks Next. NOT currently wired up (see "Mesh-based finalize
rework" above) -- it needs a labelmap and finalize no longer produces
one; wallThicknessWarningLabel stays empty/hidden until this is ported to
work on a mesh, or the labelmap path is restored. See config.py's
"post-segmentation wall-thickness warning" section for why no threshold
choice can fix the underlying failure mode this check flags.
"""

from __future__ import annotations
import os
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from EarReconstructionPlannerLib import wizard_state
from core import roi_crop, mesh_export, io_utils, threshold_seeds, wall_quality
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
            "Point' once on solid bone near the ear canal -- combined with "
            "the soft-tissue point you could place on the Pinna "
            "Segmentation page earlier, this lets 'Auto-Calibrate & "
            "Segment' start from a threshold tuned to this specific scan. "
            "This step is optional; the plain default works reasonably "
            "well without it. If the point lands in the wrong spot, use "
            "'Redo Calibration Point' to place it again.\n\n"
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
        # a fresh node on every re-entry would orphan earlier visits' nodes.
        existing = self.state.scutum_bone_seed_fiducial_node
        if existing is not None and slicer.mrmlScene.IsNodePresent(existing):
            self._seed_fiducial_node = existing
        else:
            self._seed_fiducial_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode", "ScutumBoneSeed"
            )
            self._seed_fiducial_node.SetLocked(False)
            self._seed_fiducial_node.CreateDefaultDisplayNodes()
            self.state.scutum_bone_seed_fiducial_node = self._seed_fiducial_node
        self._seed_fiducial_node.GetDisplayNode().SetVisibility(True)

        self._calib_observer_tag = None
        self._update_calibration_step_display()

    def _update_calibration_step_display(self):
        if self.state.scutum_bone_seed is not None:
            self.ui.calibrationInstructionLabel.setText("Bone calibration point placed.")
            self.ui.placeCalibrationPointButton.setEnabled(False)
        else:
            self.ui.calibrationInstructionLabel.setText(
                "Optional: click a point on solid bone near the ear canal."
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

        self.state.scutum_bone_seed = tuple(ras)
        self._seed_fiducial_node.SetNthControlPointLabel(point_index, "bone_seed")

        self._update_calibration_step_display()

        if self.state.pinna_soft_tissue_seed is not None:
            self.ui.statusLabel.setText(
                "Calibration point placed. Click 'Auto-Calibrate & "
                "Segment' to use it."
            )
        else:
            self.ui.statusLabel.setText(
                "Calibration point placed. It won't be used until you also "
                "place the soft-tissue calibration point on the Pinna "
                "Segmentation page -- or 'Auto-Calibrate & Segment' will "
                "just use the default threshold."
            )

    def _on_redo_calibration_clicked(self):
        if self._seed_fiducial_node is not None:
            self._seed_fiducial_node.RemoveAllControlPoints()
        self.state.scutum_bone_seed = None
        self._update_calibration_step_display()
        self.ui.statusLabel.setText(
            "Calibration point cleared. Place it again, or use "
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
        #
        # The crop box is built from the landmarks AND both calibration
        # seed points (when placed), not just the landmarks -- the seeds
        # are surgeon clicks on real anatomy (e.g. the scutum) that can
        # legitimately sit further from the canal_opening/near_eardrum
        # axis than this coarse crop's old fixed landmark-only margin
        # covered, which was surfacing as a spurious "calibration point
        # fell outside the scan region" error even though the click was
        # perfectly valid. Since the seed points now directly define the
        # box, they can never fall outside their own crop.
        sitk_image = io_utils.flip_ras_lps(sitkUtils.PullVolumeFromSlicer(self.state.volume_node))
        calibration_points = [
            self.state.scutum_landmarks.canal_opening,
            self.state.scutum_landmarks.near_eardrum,
        ]
        if self.state.scutum_bone_seed is not None:
            calibration_points.append(self.state.scutum_bone_seed)
        if self.state.pinna_soft_tissue_seed is not None:
            calibration_points.append(self.state.pinna_soft_tissue_seed)
        coarse_cropped = roi_crop.crop_to_points_region(sitk_image, calibration_points)

        if self.state.scutum_bone_seed is not None and self.state.pinna_soft_tissue_seed is not None:
            try:
                bone_threshold = threshold_seeds.calibrate_bone_threshold(
                    coarse_cropped, self.state.scutum_bone_seed, self.state.pinna_soft_tissue_seed
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
        # Currently unused -- needs a labelmap, and _finalize_pipeline no
        # longer produces one. See module docstring's "Mesh-based finalize
        # rework" for the known gap this leaves. Left in place (and still
        # correct) in case this is wired back up against a mesh-derived
        # voxelization, or the labelmap path is restored.
        warning = wall_quality.check_wall_thickness(bone_wall_sitk_image)
        self.ui.wallThicknessWarningLabel.setText(warning or "")
        self.ui.wallThicknessWarningLabel.setVisible(bool(warning))

    def _export_segment_as_trimesh(self, segmentation_node, slicer):
        """Pulls the segmentation's own closed-surface representation --
        the same vtkDiscreteFlyingEdges3D + vtkWindowedSincPolyDataFilter
        mesh Slicer already auto-generates for the live 3D view -- out as
        a trimesh.Trimesh, instead of re-deriving a mesh from scratch via
        a fresh marching_cubes pass on a re-exported labelmap. See this
        file's module docstring for why (the 2026-07-31 finalize-mesh-
        quality investigation: the old approach's quality mismatch traced
        to running a genuinely different meshing/smoothing algorithm than
        what the surgeon approved live, not to any actual precision loss
        in the labelmap export step itself).

        Uses ExportVisibleSegmentsToModels (confirmed via Slicer's own
        script-repository docs, alongside ExportAllSegmentsToModels, to
        produce model nodes in world/RAS coordinates) rather than reading
        GetClosedSurfaceRepresentation()/GetClosedSurfaceInternalRepresentation()
        directly, which those same docs note return polydata in the
        segmentation node's OWN internal coordinate system, needing a
        manual parent-transform correction -- not a risk worth taking on a
        project with this much RAS/LPS history (see CLAUDE.md's "Key
        lessons" section). NOT yet real-Slicer confirmed. If
        roi_crop.points_inside_roi() downstream ever comes back completely
        empty despite a clearly-good live segmentation, check here first:
        it's the one place a coordinate-frame mistake could hide (a wrong
        frame would likely misalign vertices against the RAS-space
        landmarks enough that the ROI crop finds nothing, rather than
        silently succeeding with a shifted mesh -- but that's a plausible
        failure mode, not a guarantee).
        """
        import vtk

        segmentation_node.CreateClosedSurfaceRepresentation()

        sh_node = slicer.mrmlScene.GetSubjectHierarchyNode()
        export_folder_id = sh_node.CreateFolderItem(
            sh_node.GetSceneItemID(), "ScutumFinalizeMeshExportTemp"
        )
        model_nodes = []
        try:
            slicer.modules.segmentations.logic().ExportVisibleSegmentsToModels(
                segmentation_node, export_folder_id
            )
            child_ids = vtk.vtkIdList()
            sh_node.GetItemChildren(export_folder_id, child_ids)
            model_nodes = [
                sh_node.GetItemDataNode(child_ids.GetId(i))
                for i in range(child_ids.GetNumberOfIds())
            ]
            model_nodes = [node for node in model_nodes if node is not None]
            if not model_nodes:
                raise mesh_export.EmptySegmentationError(
                    "The segmentation is empty -- no bone wall was found."
                )
            polydata = model_nodes[0].GetPolyData()
            return self._polydata_to_trimesh(polydata)
        finally:
            # Only the returned trimesh copy is kept -- clean up the
            # temporary scene nodes this export created.
            for node in model_nodes:
                slicer.mrmlScene.RemoveNode(node)
            sh_node.RemoveItem(export_folder_id)

    @staticmethod
    def _polydata_to_trimesh(polydata):
        """Converts a vtkPolyData (assumed all-triangle, as Slicer's
        closed-surface conversion produces) into a trimesh.Trimesh."""
        import trimesh
        from vtk.util.numpy_support import vtk_to_numpy

        points = vtk_to_numpy(polydata.GetPoints().GetData())
        cells = vtk_to_numpy(polydata.GetPolys().GetData())
        if cells.size == 0 or cells.size % 4 != 0:
            raise ValueError(
                "Expected an all-triangle closed-surface mesh from Slicer's "
                "segmentation converter -- got an empty or mixed-polygon "
                "cell array, which this conversion doesn't handle."
            )
        faces = cells.reshape(-1, 4)[:, 1:4]
        return trimesh.Trimesh(vertices=points, faces=faces, process=True)

    def _on_finalize_clicked(self):
        import sitkUtils
        import slicer

        self.ui.finalizeButton.setEnabled(False)
        self.ui.progressBar.setVisible(True)
        self.ui.progressBar.setMinimum(0)
        self.ui.progressBar.setMaximum(3)
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
        self.ui.progressBar.setMaximum(3)
        self.ui.progressBar.setValue(0)
        try:
            return self._finalize_pipeline(sitkUtils, slicer)
        finally:
            self.ui.progressBar.setVisible(False)

    def _finalize_pipeline(self, sitkUtils, slicer):
        """Derives the final drawable mesh directly from the live
        segmentation's OWN closed-surface representation -- the same mesh
        Slicer already auto-generates for the live 3D view while editing
        -- cropped to the precise ROI and restricted to the component
        nearest the canal axis, both done in mesh space. See this file's
        module docstring ("Mesh-based finalize rework", 2026-07-31) for
        why this replaced the previous labelmap-export + marching_cubes
        approach, and for the deliberately-accepted experimental gaps
        (no postprocess -- no speck removal/hole fill/tunnel closing; no
        thin-wall check).

        Always re-derives fresh from the segmentation node's current
        content (never re-processes an already-finalized mesh), so this
        stays safe against the "unconditional reprocessing erodes an
        already-good result" failure mode documented in CLAUDE.md's
        Isolate Patch saga -- the segmentation node itself, not a prior
        mesh, is the single source of truth here.

        Known tradeoff, unchanged from before this rework: keeping only
        the single connected component nearest the canal axis means a
        manually-painted addition that isn't connected to the main wall
        gets discarded. If a surgeon needs to keep a deliberately-separate
        painted region, the Islands effect (in the curated effect list
        above) is the tool for merging/managing that before clicking
        Preview/Next.
        """
        wizard_state.clear_downstream_state(self.state, "scutum_review")

        self.ui.wallThicknessWarningLabel.setText("")
        self.ui.wallThicknessWarningLabel.setVisible(False)

        segmentation_node = self.state.scutum_bone_wall_segmentation_node
        if segmentation_node is None or segmentation_node.GetSegmentation().GetNumberOfSegments() == 0:
            message = "Nothing segmented yet -- click 'Auto-Calibrate & Segment' first."
            self.ui.statusLabel.setText(message)
            return False, message

        self.ui.statusLabel.setText("Reading current segmentation as a 3D surface...")
        slicer.app.processEvents()

        # NOT wrapped in run_blocking(): unlike the crop/select step below
        # (pure trimesh/numpy), this touches the MRML scene directly
        # (subject hierarchy + node creation/removal), which per this
        # project's established run_blocking() safety rule (see
        # CLAUDE.md's "Pinna UI responsiveness fix") must only ever happen
        # on the main thread. Expected to be fast regardless -- Slicer's
        # closed-surface representation is normally already computed/
        # cached for a visible segmentation node, so this is mostly
        # cheap bookkeeping, not a fresh multi-second computation.
        try:
            mesh = self._export_segment_as_trimesh(segmentation_node, slicer)
        except mesh_export.EmptySegmentationError:
            message = (
                "No bone wall found. Try lowering the threshold minimum, "
                "or painting some material in directly."
            )
            self.ui.statusLabel.setText(message)
            return False, message
        self.ui.progressBar.setValue(1)

        # Mesh-space crop-to-ROI + nearest-to-axis component selection --
        # both pure trimesh/numpy, safe to run in the background.
        def _crop_and_select():
            keep_mask = roi_crop.points_inside_roi(mesh.vertices, self.state.scutum_landmarks)
            cropped = mesh_export.crop_mesh_to_vertex_mask(mesh, keep_mask)
            if len(cropped.faces) == 0:
                return None
            return mesh_export.select_mesh_component_nearest_axis(
                cropped,
                self.state.scutum_landmarks.canal_opening,
                self.state.scutum_landmarks.near_eardrum,
            )

        final_mesh = self.run_blocking(
            _crop_and_select, status_text="Cropping to the canal wall..."
        )
        self.ui.progressBar.setValue(2)

        if final_mesh is None:
            message = (
                "No material found inside the region of interest. Go back to "
                "the Threshold effect and lower the minimum, or paint some "
                "material in directly."
            )
            self.ui.statusLabel.setText(message)
            return False, message

        mesh_path = os.path.join(
            self.state.working_dir or slicer.app.temporaryPath, "scutum_bone_wall.stl"
        )
        mesh_export.export_mesh(final_mesh, mesh_path)
        self.state.scutum_bone_wall_mesh_path = mesh_path

        if self.state.scutum_bone_wall_model_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.scutum_bone_wall_model_node)
        self.state.scutum_bone_wall_model_node = slicer.util.loadModel(mesh_path)
        self.ui.progressBar.setValue(3)

        # Hide the live segmentation now that the finalized drawable model
        # is showing the same thing -- avoids visually duplicating it in
        # the 3D view.
        segmentation_node.GetDisplayNode().SetVisibility(False)

        if self.state.scutum_landmarks_fiducial_node is not None:
            self.state.scutum_landmarks_fiducial_node.GetDisplayNode().SetVisibility(False)
        if self.state.scutum_bone_seed_fiducial_node is not None:
            self.state.scutum_bone_seed_fiducial_node.GetDisplayNode().SetVisibility(False)

        self.recenter_3d_view()

        self._needs_finalize = False
        self.ui.statusLabel.setText(
            "3D surface ready. Review it in the 3D view -- go back to the "
            "Threshold/Paint/Erase tools above and click 'Preview 3D Result' "
            "again if you want to adjust it further, or click Next to continue."
        )
        return True, ""

    def _on_reset_page_clicked(self):
        # clear_page_state already resets scutum_bone_seed/
        # scutum_bone_seed_fiducial_node/scutum_bone_wall_segmentation_node
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
