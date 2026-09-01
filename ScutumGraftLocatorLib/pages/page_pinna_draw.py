"""
page_pinna_draw.py
=====================
Page 5: the surgeon draws a closed outline around the pinna on the 3D
skin-surface mesh; the enclosed patch is extracted as the isolated pinna
mesh for Curvature Project v4.

Structurally identical to page_scutum_draw.py -- same surface-constrained
curve + seed point + core/mesh_isolate.py extraction -- plus two pinna-only
extras: a "Mark Canal Opening" point and an automatic crop toward the head
interior.

Why the extra canal marker: the pinna's segmented mesh starts out fused to
the surrounding head/scalp skin (they're physically continuous tissue), and
the drawn outline alone doesn't always fully separate "just the pinna" from
a sliver of that head-side attachment right where the pinna meets the skull
near the ear canal. Reusing the *ear canal's* own canal_opening landmark
(from the scutum landmarks page) as the cut-plane position wasn't reliable -- it was
placed on a different mesh/context and doesn't necessarily sit far enough
outward relative to this specific pinna geometry. So the surgeon marks a
fresh point directly on THIS pinna model, at the ear canal opening, used
only as the cut plane's position; the ear canal's own landmarks are still
used, but only for the "which way is into the head" direction, which is a
stable anatomical fact independent of exactly where the plane needs to
sit. After that crop, core/mesh_isolate.keep_connected_component_containing()
discards any head-interior material left over as a disconnected island
(the crop doesn't always cleanly sever it in one piece), keeping only the
component still connected to the surgeon's own seed point.

Expected widgets in page_pinna_draw.ui:
  - instructionLabel        (QLabel)
  - tutorialLabel           (QLabel) -- extra guidance, shown only in tutorial mode
  - startCurveButton        (QPushButton)
  - markSeedButton          (QPushButton)
  - markCanalOpeningButton  (QPushButton)
  - isolateButton           (QPushButton)
  - resetPageButton         (QPushButton) -- clear this page's outline/patch
  - revertToHereButton      (QPushButton) -- clear every later step, keep this patch
  - progressBar             (QProgressBar) -- shown only while a background step (see
    base_page.WizardPage.run_blocking()) is running, hidden otherwise
  - statusLabel             (QLabel)
"""

from __future__ import annotations
import os
import numpy as np
from ScutumGraftLocatorLib.pages.base_page import WizardPage
from ScutumGraftLocatorLib import wizard_state
from core import mesh_isolate, mesh_export, postprocess, io_utils, roi_crop
import config

# Reused in on_enter() and _on_reset_page_clicked() so the tutorial text
# always goes back to this step-1 guidance when the outline is cleared,
# instead of staying stuck on whatever later step it last showed.
_STEP1_TUTORIAL_TEXT = (
    "Step 1 of 4 -- draw the outline. Important: once you click 'Start "
    "Outline', left-click is used only for adding points, so you can't "
    "rotate the 3D view anymore until you finish the loop -- get the "
    "camera angle you want *before* clicking 'Start Outline', so you can "
    "clearly see where the pinna meets the rest of the head. While "
    "drawing, you can still zoom (scroll) and pan (middle-drag) without "
    "adding extra points; only left-clicking adds one, and right-clicking "
    "finishes the loop (see below), so don't use it to zoom while drawing.\n\n"
    "Usually you want to isolate just the outer (lateral) face of the "
    "pinna -- trace along the helix (the curled outer rim) so the outline "
    "follows the visible outer edge of the ear, rather than wrapping "
    "around to the back of the head. If you only want to test a smaller "
    "region (e.g. one part of the pinna, not the whole thing), just draw "
    "around that smaller area instead -- the tool works the same either way.\n\n"
    "When you're done, right-click once to finish (you don't need to "
    "click back near your starting point -- this is a closed loop, so "
    "it's already connected end-to-end).\n\n"
    "After finishing, you can still adjust the outline before moving on: "
    "click and drag any point to move it, or right-click a point and "
    "choose 'Delete Control Point' to remove it."
)


class PinnaDrawPage(WizardPage):
    def on_enter(self):
        self._curve_node = None
        self._seed_fiducial_node = None
        self._seed_point = None
        self._canal_fiducial_node = None
        self._canal_point = None

        # Re-show the isolated patch (or, if not isolated yet, the
        # pre-isolation region model) in case it was hidden by the scutum
        # review page after the surgeon navigated forward into the scutum
        # stage and then back again -- see page_scutum_review.py's on_enter.
        if self.state.pinna_isolated_model_node is not None:
            self.state.pinna_isolated_model_node.GetDisplayNode().SetVisibility(True)
        elif self.state.pinna_region_model_node is not None:
            self.state.pinna_region_model_node.GetDisplayNode().SetVisibility(True)

        self.ui.startCurveButton.clicked.connect(self._on_start_curve_clicked)
        self.ui.markSeedButton.clicked.connect(self._on_mark_seed_clicked)
        self.ui.markCanalOpeningButton.clicked.connect(self._on_mark_canal_clicked)
        self.ui.isolateButton.clicked.connect(self._on_isolate_clicked)
        self.ui.resetPageButton.clicked.connect(self._on_reset_page_clicked)
        self.ui.revertToHereButton.clicked.connect(self._on_revert_to_here_clicked)
        self.ui.markSeedButton.setEnabled(False)
        self.ui.markCanalOpeningButton.setEnabled(False)
        self.ui.isolateButton.setEnabled(False)
        self.ui.progressBar.setVisible(False)
        self.ui.statusLabel.setText(
            "Click 'Start Outline', then trace around the pinna (usually "
            "just the outer face, along the helix) on the skin surface in "
            "the 3D view. Right-click to finish the loop."
        )
        self.set_tutorial_text(_STEP1_TUTORIAL_TEXT)

    def _on_start_curve_clicked(self):
        import slicer

        if self._curve_node is not None:
            slicer.mrmlScene.RemoveNode(self._curve_node)

        # Starting a new outline invalidates the old seed/canal markers --
        # see the matching comment in page_scutum_draw._on_start_curve_clicked
        # for why leaving them live is a real bug, not just tidiness.
        if self._seed_fiducial_node is not None:
            slicer.mrmlScene.RemoveNode(self._seed_fiducial_node)
        if self._canal_fiducial_node is not None:
            slicer.mrmlScene.RemoveNode(self._canal_fiducial_node)
        self._seed_fiducial_node = None
        self._seed_point = None
        self._canal_fiducial_node = None
        self._canal_point = None
        self.ui.isolateButton.setEnabled(False)

        self._curve_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLMarkupsClosedCurveNode", "PinnaOutline"
        )
        self.set_absolute_point_size(self._curve_node, config.PINNA_DRAW_POINT_SIZE_MM)
        if self.state.pinna_region_model_node is not None:
            self._curve_node.SetCurveTypeToShortestDistanceOnSurface(
                self.state.pinna_region_model_node
            )

        interaction_node = slicer.app.applicationLogic().GetInteractionNode()
        selection_node = slicer.app.applicationLogic().GetSelectionNode()
        selection_node.SetActivePlaceNodeID(self._curve_node.GetID())
        interaction_node.SetCurrentInteractionMode(interaction_node.Place)
        interaction_node.SetPlaceModePersistence(1)

        self.ui.markSeedButton.setEnabled(True)
        self.ui.markCanalOpeningButton.setEnabled(True)
        self.ui.statusLabel.setText(
            "Trace around the pinna by clicking points on the surface, then "
            "right-click to finish. When you're done, click 'Mark Inside "
            "Point' and 'Mark Canal Opening'."
        )
        self.set_tutorial_text(
            "Step 2 of 4 -- mark a point inside the loop, once it's closed "
            "(right-click to finish it, if you haven't yet). Click 'Mark "
            "Inside Point', then left-click once, anywhere on the surface "
            "inside the outline you just drew. This tells the tool which "
            "side of the boundary is the pinna to keep."
        )

    def _on_mark_seed_clicked(self):
        import slicer

        interaction_node = slicer.app.applicationLogic().GetInteractionNode()
        interaction_node.SetCurrentInteractionMode(interaction_node.ViewTransform)

        if self._seed_fiducial_node is not None:
            slicer.mrmlScene.RemoveNode(self._seed_fiducial_node)
        self._seed_fiducial_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLMarkupsFiducialNode", "PinnaSeedPoint"
        )
        self.set_absolute_point_size(self._seed_fiducial_node, config.PINNA_DRAW_POINT_SIZE_MM)

        selection_node = slicer.app.applicationLogic().GetSelectionNode()
        selection_node.SetActivePlaceNodeID(self._seed_fiducial_node.GetID())
        interaction_node.SetCurrentInteractionMode(interaction_node.Place)
        interaction_node.SetPlaceModePersistence(0)

        self._seed_observer_tag = self._seed_fiducial_node.AddObserver(
            self._seed_fiducial_node.PointPositionDefinedEvent, self._on_seed_placed
        )
        self.ui.statusLabel.setText("Click once, inside the pinna outline...")

    def _on_seed_placed(self, caller, event):
        self._seed_fiducial_node.RemoveObserver(self._seed_observer_tag)
        ras = [0.0, 0.0, 0.0]
        self._seed_fiducial_node.GetNthControlPointPositionWorld(0, ras)
        self._seed_point = tuple(ras)
        self._update_isolate_enabled()
        self.ui.statusLabel.setText("Seed point marked. Click 'Isolate Patch' when ready.")
        self.set_tutorial_text(
            "Step 3 of 4 -- mark the ear canal opening. Click 'Mark Canal "
            "Opening', then left-click once on this same pinna model, right "
            "where the canal opens (where the pinna meets the skull near "
            "the ear canal). This helps the tool trim away any bit of head "
            "skin still attached to the isolated pinna near that spot."
        )

    def _on_mark_canal_clicked(self):
        import slicer

        interaction_node = slicer.app.applicationLogic().GetInteractionNode()
        interaction_node.SetCurrentInteractionMode(interaction_node.ViewTransform)

        if self._canal_fiducial_node is not None:
            slicer.mrmlScene.RemoveNode(self._canal_fiducial_node)
        self._canal_fiducial_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLMarkupsFiducialNode", "PinnaCanalOpeningMarker"
        )
        self.set_absolute_point_size(self._canal_fiducial_node, config.PINNA_DRAW_POINT_SIZE_MM)

        selection_node = slicer.app.applicationLogic().GetSelectionNode()
        selection_node.SetActivePlaceNodeID(self._canal_fiducial_node.GetID())
        interaction_node.SetCurrentInteractionMode(interaction_node.Place)
        interaction_node.SetPlaceModePersistence(0)

        self._canal_observer_tag = self._canal_fiducial_node.AddObserver(
            self._canal_fiducial_node.PointPositionDefinedEvent, self._on_canal_placed
        )
        self.ui.statusLabel.setText(
            "Click once, at the opening of the ear canal (where it meets the pinna)..."
        )

    def _on_canal_placed(self, caller, event):
        self._canal_fiducial_node.RemoveObserver(self._canal_observer_tag)
        ras = [0.0, 0.0, 0.0]
        self._canal_fiducial_node.GetNthControlPointPositionWorld(0, ras)
        self._canal_point = tuple(ras)
        self._update_isolate_enabled()
        self.ui.statusLabel.setText("Canal opening marked. Click 'Isolate Patch' when ready.")
        self.set_tutorial_text(
            "Step 4 of 4 -- click 'Isolate Patch' to extract the pinna as "
            "its own mesh. If this fails with a message about too few "
            "points, the outline likely didn't fully close or the clicks "
            "landed too close together -- click 'Reset This Page' and "
            "redraw it, spacing your clicks out a bit more."
        )

    def _update_isolate_enabled(self):
        self.ui.isolateButton.setEnabled(
            self._seed_point is not None and self._canal_point is not None
        )

    def _on_isolate_clicked(self):
        import slicer

        if self._curve_node is None or self._seed_point is None or self._canal_point is None:
            self.ui.statusLabel.setText(
                "Please draw an outline, mark a seed point, and mark the canal "
                "opening first."
            )
            return

        # Steps below run on a background thread via self.run_blocking() --
        # see that method's docstring in base_page.py and the matching
        # comment in page_pinna_review.py's _on_run_clicked(). In short:
        # isolate_surface_patch() alone runs ~5-6 seconds on a real scan
        # (confirmed via [isolate timing] instrumentation), and the
        # LoopDoesNotSeparateError fallback path below can run a further
        # ~30+ seconds (a full postprocess+remesh round trip) -- both long
        # enough to make Slicer's window look "Not Responding" if run
        # directly on this (main) thread. self._progress_step tracks
        # progress across both this method and _build_fallback_mesh() (the
        # fallback path only runs if the first isolate attempt fails, so
        # the total step count -- and progressBar's max -- isn't known
        # upfront; bumped up if/when the fallback path actually triggers).
        self.ui.isolateButton.setEnabled(False)
        self.ui.progressBar.setVisible(True)
        self.ui.progressBar.setMinimum(0)
        self.ui.progressBar.setMaximum(3)
        self.ui.progressBar.setValue(0)
        self._progress_step = 0
        self._progress_max = 3
        try:
            self._run_isolate_pipeline()
        finally:
            self.ui.isolateButton.setEnabled(True)
            self.ui.progressBar.setVisible(False)

    def _advance_progress(self, label):
        # Tracks the max as a plain Python attribute (self._progress_max)
        # rather than reading it back from the Qt widget -- this pipeline's
        # real step count isn't known upfront (the LoopDoesNotSeparateError
        # fallback path below adds steps only if it actually triggers), so
        # the bar's max is bumped up here whenever more steps turn out to
        # be needed than originally set.
        self._progress_step += 1
        if self._progress_step > self._progress_max:
            self._progress_max = self._progress_step
            self.ui.progressBar.setMaximum(self._progress_max)
        self.ui.progressBar.setValue(self._progress_step)
        self.ui.statusLabel.setText(label)

    def _run_isolate_pipeline(self):
        import slicer
        import trimesh

        self.ui.statusLabel.setText("Isolating pinna patch...")
        slicer.app.processEvents()

        # Use the curve's dense, interpolated surface path (not just the
        # sparse raw control points) as the loop to isolate against -- see
        # the matching comment in page_scutum_draw.py's _on_isolate_clicked
        # for why: GetCurvePointsWorld() already hugs the mesh surface at
        # much higher density than the raw clicks, so the isolated patch's
        # boundary tracks the drawn shape far more closely than bridging
        # sparse control points with mesh-graph shortest paths does.
        curve_points = []
        curve_points_world = self._curve_node.GetCurvePointsWorld()
        for i in range(curve_points_world.GetNumberOfPoints()):
            curve_points.append(tuple(curve_points_world.GetPoint(i)))

        # TEMPORARY DIAGNOSTICS (2026-07-27) -- see CLAUDE.md Known Issues
        # #18 follow-up. Checks whether Slicer's closed-curve auto-close
        # segment (connecting the last click back to the first, since the
        # surgeon isn't required to close the loop by hand) is a sane,
        # short hop or an unexpectedly large jump -- a big jump here would
        # mean the "closing" part of the loop cuts across the mesh in a
        # way that doesn't actually trace the intended boundary, which
        # would explain the seed point ending up on the wrong side of the
        # loop entirely.
        import numpy as _np
        _pts = _np.asarray(curve_points)
        _consecutive = _np.linalg.norm(_np.diff(_pts, axis=0), axis=1)
        _wrap = _np.linalg.norm(_pts[0] - _pts[-1])
        _all_gaps = _np.append(_consecutive, _wrap)
        _max_gap_idx = int(_np.argmax(_all_gaps))
        print(
            f"[isolate diag] drawn curve: {len(curve_points)} raw points, "
            f"median consecutive spacing {_np.median(_consecutive):.2f}mm, "
            f"largest gap {_all_gaps[_max_gap_idx]:.2f}mm "
            f"(at index {_max_gap_idx}{'=wrap-around/auto-close' if _max_gap_idx == len(_all_gaps) - 1 else ''})"
        )

        self._advance_progress("Loading pinna mesh...")
        mesh = self.run_blocking(lambda: trimesh.load(self.state.pinna_region_mesh_path))
        # The STL file on disk is LPS-numbered (see mesh_export.export_mesh's
        # docstring for why) -- flip back to RAS so mesh.vertices matches
        # curve_points/self._seed_point/self._canal_point, which are always
        # RAS from Slicer's Markups nodes.
        mesh.vertices = mesh_export.flip_ras_lps_points(mesh.vertices)

        # Crop toward the ear canal FIRST, before isolating -- Thomas's
        # suggestion (2026-07-27), after several rounds chasing genuine
        # topological handles (mesh.euler_number well below 2, confirmed
        # via [isolate diag]) that survived multiple rounds of
        # postprocessing fixes. The head-interior material this discards
        # was always going to be thrown away eventually (this same crop
        # used to run AFTER isolate_surface_patch, on the already-isolated
        # patch) -- cropping first means whatever handles/tunnels are
        # hiding in that interior material (very plausibly the ear canal's
        # own tube-like anatomy, or postprocessing noise deep in the
        # ROI's less-visible interior) are gone before
        # isolate_surface_patch ever has to deal with them. Also has a
        # useful side effect even for handles that aren't fully inside
        # the discarded region: any handle whose "loop" crosses the cut
        # plane gets severed into a harmless open boundary edge instead of
        # remaining a genus-raising tunnel -- isolate_surface_patch's
        # vertex-removal flood fill doesn't care about open boundaries at
        # all, only about handles.
        self._advance_progress("Cropping toward the ear canal...")
        try:
            mesh = self.run_blocking(lambda: self._crop_toward_canal_if_possible(mesh))
        except ValueError as e:
            self.ui.statusLabel.setText(str(e))
            return

        def _snap_and_isolate(m):
            loop_indices = mesh_isolate.snap_points_to_vertices(m, curve_points)
            return mesh_isolate.isolate_surface_patch(m, loop_indices, self._seed_point)

        self._advance_progress("Isolating the drawn patch...")
        try:
            patch = self.run_blocking(lambda: _snap_and_isolate(mesh))
        except mesh_isolate.LoopDoesNotSeparateError as e:
            # Confirmed on a real scan (2026-07-27) that a perfectly-drawn,
            # densely-sampled, non-self-intersecting loop can still fail to
            # separate this mesh -- likely an unrelated thin "handle"
            # elsewhere on the surface (two nearby folds close enough to
            # register as touching), more likely now that the mesh only
            # goes through postprocessing once (see page_pinna_review.py's
            # _segmentation_edited guard, added to stop that same
            # postprocessing from eroding the pinna's own helix when
            # applied twice). Rather than re-erode the reviewed mesh by
            # default to guard against this, retry just this isolate
            # attempt against a more heavily processed version of the same
            # underlying segmentation -- using the SAME already-drawn
            # curve_points/seed_point, so the surgeon never has to redraw
            # anything. This mirrors the old, previously-reliable
            # behavior (the mesh used to always go through postprocessing
            # twice by the time it reached this page) but only pays that
            # cost when actually needed.
            self.ui.statusLabel.setText(
                "That outline didn't isolate cleanly -- retrying against a "
                "more thoroughly cleaned-up version of the segmentation..."
            )
            slicer.app.processEvents()
            fallback_mesh = self._build_fallback_mesh()
            if fallback_mesh is None:
                self.ui.statusLabel.setText(str(e))
                return
            try:
                # Crop the fallback mesh too, same reasoning as the
                # primary attempt above.
                self._advance_progress("Cropping fallback mesh toward the ear canal...")
                fallback_mesh = self.run_blocking(
                    lambda: self._crop_toward_canal_if_possible(fallback_mesh)
                )
                self._advance_progress("Retrying isolation...")
                patch = self.run_blocking(lambda: _snap_and_isolate(fallback_mesh))
            except ValueError:
                # The fallback didn't help either -- report the original
                # error, since it's the more informative one (mentions the
                # actual mesh-topology explanation, not a generic failure).
                self.ui.statusLabel.setText(str(e))
                return
        except ValueError as e:
            # Any other failure (too few points, a gap enclosing zero
            # vertices, a disconnected seed) needs the surgeon to actually
            # redraw or re-click -- not retry-worthy, unlike
            # LoopDoesNotSeparateError above.
            self.ui.statusLabel.setText(str(e))
            return

        # Note: the head-interior crop used to happen here, AFTER
        # isolation -- it now happens BEFORE, on the whole pre-isolation
        # mesh (see _crop_toward_canal_if_possible() above), so there's
        # nothing left to crop from `patch` at this point.

        output_path = os.path.join(
            self.state.working_dir or slicer.app.temporaryPath, "pinna_isolated.stl"
        )
        # Use mesh_export.export_mesh (not patch.export directly) so the
        # RAS->LPS flip is applied consistently -- otherwise this file
        # would display mirrored when reloaded via slicer.util.loadModel.
        mesh_export.export_mesh(patch, output_path)
        self.state.pinna_isolated_mesh_path = output_path

        if self.state.pinna_isolated_model_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.pinna_isolated_model_node)
        self.state.pinna_isolated_model_node = slicer.util.loadModel(output_path)

        # Hide the original (pre-isolation) skin-surface model now that the
        # isolated pinna patch exists, so they don't overlap/clutter the
        # 3D view.
        if self.state.pinna_region_model_node is not None:
            self.state.pinna_region_model_node.GetDisplayNode().SetVisibility(False)

        # Hide the drawn outline curve and the seed/canal-opening marker
        # points now that the patch has been extracted -- they've served
        # their purpose and just clutter the 3D view on top of the isolated
        # result otherwise.
        if self._curve_node is not None:
            self._curve_node.GetDisplayNode().SetVisibility(False)
        if self._seed_fiducial_node is not None:
            self._seed_fiducial_node.GetDisplayNode().SetVisibility(False)
        if self._canal_fiducial_node is not None:
            self._canal_fiducial_node.GetDisplayNode().SetVisibility(False)

        self.ui.statusLabel.setText(
            f"Pinna isolated ({len(patch.vertices)} vertices). "
            "Check it in the 3D view, then click Next."
        )

    def _crop_toward_canal_if_possible(self, mesh):
        """Crops `mesh` toward the interior of the head using the
        surgeon's canal-opening marker (self._canal_point) and the ear
        canal's own axis direction (from the earlier scutum landmarks
        page), then keeps only whatever's still connected to the seed
        point. No-ops (returns `mesh` unchanged) if the ear canal
        landmarks aren't available.

        Moved to run BEFORE isolate_surface_patch (2026-07-27, Thomas's
        suggestion) -- see the comment at its call site in
        _on_isolate_clicked() for why cropping first, rather than after,
        helps with genuine mesh-topology handles."""
        canal_landmarks = self.state.scutum_landmarks
        if canal_landmarks.canal_opening is None or canal_landmarks.near_eardrum is None:
            return mesh
        axis_direction = np.array(canal_landmarks.near_eardrum) - np.array(
            canal_landmarks.canal_opening
        )
        mesh = mesh_isolate.crop_toward_canal(mesh, self._canal_point, axis_direction)
        # The crop can leave severed head-interior material as a
        # disconnected island rather than removing it in one clean cut --
        # keep only the piece still connected to the surgeon's own seed
        # point.
        return mesh_isolate.keep_connected_component_containing(mesh, self._seed_point)

    def _build_fallback_mesh(self):
        """Re-derives a more heavily postprocessed pinna mesh from the
        current segmentation node, for isolate_surface_patch() to retry
        against after a LoopDoesNotSeparateError. Purely local to this one
        isolate attempt -- does NOT touch state.pinna_region_mesh_path or
        state.pinna_region_model_node, so the Review page's displayed mesh
        (and its helix, protected by only postprocessing once -- see
        page_pinna_review.py) is never affected by this fallback. Mirrors
        page_pinna_review.py's _refresh_mesh_from_segmentation(), minus
        the parts that write to a file/load a model, since this mesh is
        only needed in memory, transiently, here.

        Returns None if there's no segmentation node to rebuild from, or
        if the rebuilt result is empty -- both treated as "fallback not
        available" by the caller.
        """
        import slicer
        import sitkUtils

        segmentation_node = self.state.pinna_region_segmentation_node
        if segmentation_node is None:
            return None

        temp_label_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "PinnaRegionFallback"
        )
        try:
            slicer.modules.segmentations.logic().ExportVisibleSegmentsToLabelmapNode(
                segmentation_node, temp_label_node
            )
            sitk_image = io_utils.flip_ras_lps(sitkUtils.PullVolumeFromSlicer(temp_label_node))
        finally:
            slicer.mrmlScene.RemoveNode(temp_label_node)

        # Same badly-anisotropic-scan fix as page_pinna_review.py's
        # _run_segmentation_pipeline()/_refresh_mesh_from_segmentation() --
        # postprocess's own voxel-radius-based morphology below needs the
        # better-conditioned grid too, not just the final marching_cubes
        # call. No-ops on an already-near-isotropic scan.
        sitk_image = io_utils.resample_to_bounded_anisotropy(
            sitk_image, config.MESH_MAX_ANISOTROPY_RATIO, is_label=True
        )

        # A second postprocessing pass, on top of whatever's already baked
        # into the segmentation -- this is exactly the "double
        # postprocessing" that used to always happen by the time a
        # surgeon reached this page (before page_pinna_review.py's
        # _segmentation_edited guard stopped it from happening
        # unconditionally). It was found to erode the helix when applied
        # to the *reviewed/displayed* mesh -- but used only transiently
        # here, to recover a working isolate result, that tradeoff is
        # worth it: an isolate failure blocks the whole wizard, while this
        # fallback mesh is discarded immediately after use.
        #
        # This whole method is only reached from _run_isolate_pipeline()'s
        # LoopDoesNotSeparateError handler, which already put the page into
        # background-thread/progress-bar mode -- see self._advance_progress()
        # and run_blocking() (base_page.py) for why each of these steps
        # (each individually multi-second on a real scan, same cost as
        # page_pinna_review.py's own postprocess/meshing pipeline) runs on
        # a background thread here too, rather than blocking this thread
        # directly.
        self._advance_progress("Cleaning up segmentation...")
        sitk_image = self.run_blocking(
            lambda: postprocess.run_full_postprocess(sitk_image, close_tunnels=True)
        )
        self._advance_progress("Cropping to tight bounding box...")
        sitk_image = self.run_blocking(
            lambda: roi_crop.crop_to_own_bounding_box(sitk_image, config.PINNA_TIGHT_CROP_MARGIN_MM)
        )

        self._advance_progress("Building 3D surface mesh...")
        try:
            mesh = self.run_blocking(
                lambda: mesh_export.label_map_to_mesh(
                    sitk_image, max_anisotropy_ratio=config.MESH_MAX_ANISOTROPY_RATIO
                )
            )
        except mesh_export.EmptySegmentationError:
            return None

        if len(mesh.vertices) == 0:
            return None
        self._advance_progress("Simplifying mesh...")
        return self.run_blocking(lambda: mesh_export.decimate_to_target_resolution(mesh))

    def _on_reset_page_clicked(self):
        import slicer

        if self._curve_node is not None:
            slicer.mrmlScene.RemoveNode(self._curve_node)
        if self._seed_fiducial_node is not None:
            slicer.mrmlScene.RemoveNode(self._seed_fiducial_node)
        if self._canal_fiducial_node is not None:
            slicer.mrmlScene.RemoveNode(self._canal_fiducial_node)
        self._curve_node = None
        self._seed_fiducial_node = None
        self._seed_point = None
        self._canal_fiducial_node = None
        self._canal_point = None

        wizard_state.clear_page_state(self.state, "pinna_draw")

        if self.state.pinna_region_model_node is not None:
            self.state.pinna_region_model_node.GetDisplayNode().SetVisibility(True)

        self.ui.markSeedButton.setEnabled(False)
        self.ui.markCanalOpeningButton.setEnabled(False)
        self.ui.isolateButton.setEnabled(False)
        self.ui.statusLabel.setText(
            "Outline cleared. Click 'Start Outline', then trace around the "
            "pinna on the skin surface in the 3D view."
        )
        self.set_tutorial_text(_STEP1_TUTORIAL_TEXT)

    def _on_revert_to_here_clicked(self):
        import slicer

        if wizard_state.has_downstream_state(self.state, "pinna_draw"):
            if not slicer.util.confirmYesNoDisplay(
                "This will clear every step after this one (the scutum "
                "segmentation and outline, verification, and the heatmap "
                "result). This isolated patch is kept. Continue?"
            ):
                return
            wizard_state.clear_downstream_state(self.state, "pinna_draw")
        self.ui.statusLabel.setText(
            "Later steps cleared. Go to Next when ready to redo them."
        )

    def on_leave_next(self):
        if self.state.pinna_isolated_mesh_path is None:
            return False, "Please draw the pinna outline and isolate the patch before continuing."
        return True, ""
