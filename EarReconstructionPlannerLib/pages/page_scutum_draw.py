"""
page_scutum_draw.py
======================
Page 4: the surgeon draws a closed outline directly on the 3D bone-wall
mesh to mark the scutum defect boundary; the enclosed patch is extracted
as an open-surface mesh for Curvature Project v4.

Uses a Slicer Markups closed curve constrained to the mesh surface -- as
the surgeon clicks, each point snaps onto the mesh rather than floating in
space. Once the loop is closed, one more click marks a point inside the
loop (to tell core/mesh_isolate.py which side to keep), and "Isolate
Patch" runs the extraction.

NOTE: `SetCurveTypeToShortestDistanceOnSurface(modelNode)` below is the
Slicer Markups API for constraining a curve to a model surface --
confirmed via Slicer's own vtkMRMLMarkupsCurveNode header/API docs.
Calling `SetAndObserveSurfaceConstraintNode()` alone (an earlier version
of this code did that) registers the model but does nothing on its own --
the curve type has to actually be set to ShortestDistanceOnSurface for
points to be constrained to the surface; that convenience method sets
both in one call.

Expected widgets in page_scutum_draw.ui:
  - instructionLabel    (QLabel)
  - tutorialLabel       (QLabel) -- extra guidance, shown only in tutorial mode
  - startCurveButton    (QPushButton)
  - markSeedButton      (QPushButton) -- "Mark a point inside the outline"
  - isolateButton       (QPushButton)
  - resetPageButton     (QPushButton) -- clear this page's outline/patch
  - revertToHereButton  (QPushButton) -- clear every later step, keep this patch
  - statusLabel         (QLabel)
"""

from __future__ import annotations
import os
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from EarReconstructionPlannerLib import wizard_state
from core import mesh_isolate, mesh_export
import config

# Reused in on_enter() and _on_reset_page_clicked() so the tutorial text
# always goes back to this step-1 guidance when the outline is cleared,
# instead of staying stuck on whatever later step it last showed.
_STEP1_TUTORIAL_TEXT = (
    "Step 1 of 3 -- draw the outline. Important: once you click 'Start "
    "Outline', left-click is used only for adding points, so you can't "
    "rotate the 3D view anymore until you finish the loop -- get the "
    "camera angle you want *before* clicking 'Start Outline'. While "
    "drawing, you can still zoom (scroll) and pan (middle-drag) without "
    "adding extra points; only left-clicking adds one, and right-clicking "
    "finishes the loop (see below), so don't use it to zoom while drawing.\n\n"
    "Left-click a series of points directly on the bone surface, tracing "
    "all the way around the defect -- each click snaps onto the mesh. "
    "When you're done, right-click once to finish (you don't need to "
    "click back near your starting point -- this is a closed loop, so "
    "it's already connected end-to-end).\n\n"
    "After finishing, you can still adjust the outline before moving on: "
    "click and drag any point to move it, or right-click a point and "
    "choose 'Delete Control Point' to remove it."
)


class ScutumDrawPage(WizardPage):
    def on_enter(self):
        self._curve_node = None
        self._seed_fiducial_node = None
        self._seed_point = None

        self.ui.startCurveButton.clicked.connect(self._on_start_curve_clicked)
        self.ui.markSeedButton.clicked.connect(self._on_mark_seed_clicked)
        self.ui.isolateButton.clicked.connect(self._on_isolate_clicked)
        self.ui.resetPageButton.clicked.connect(self._on_reset_page_clicked)
        self.ui.revertToHereButton.clicked.connect(self._on_revert_to_here_clicked)
        self.ui.markSeedButton.setEnabled(False)
        self.ui.isolateButton.setEnabled(False)
        self.ui.statusLabel.setText(
            "Click 'Start Outline', then trace the defect boundary on the "
            "bone surface in the 3D view. Right-click to finish the loop."
        )
        self.set_tutorial_text(_STEP1_TUTORIAL_TEXT)

    def _on_start_curve_clicked(self):
        import slicer

        if self._curve_node is not None:
            slicer.mrmlScene.RemoveNode(self._curve_node)

        # Starting a new outline invalidates any seed point marked for the
        # old one -- without this, a stale seed point silently pairs with
        # the brand-new outline and "Isolate Patch" can stay wrongly
        # enabled against a seed that no longer means anything.
        if self._seed_fiducial_node is not None:
            slicer.mrmlScene.RemoveNode(self._seed_fiducial_node)
        self._seed_fiducial_node = None
        self._seed_point = None
        self.ui.isolateButton.setEnabled(False)

        self._curve_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLMarkupsClosedCurveNode", "ScutumDefectOutline"
        )
        self.set_absolute_point_size(self._curve_node, config.SCUTUM_DRAW_POINT_SIZE_MM)
        if self.state.scutum_bone_wall_model_node is not None:
            self._curve_node.SetCurveTypeToShortestDistanceOnSurface(
                self.state.scutum_bone_wall_model_node
            )

        interaction_node = slicer.app.applicationLogic().GetInteractionNode()
        selection_node = slicer.app.applicationLogic().GetSelectionNode()
        selection_node.SetActivePlaceNodeID(self._curve_node.GetID())
        interaction_node.SetCurrentInteractionMode(interaction_node.Place)
        interaction_node.SetPlaceModePersistence(1)  # stay in place mode for multiple clicks

        self.ui.markSeedButton.setEnabled(True)
        self.ui.statusLabel.setText(
            "Trace the outline by clicking points on the surface, then "
            "right-click to finish. When you're done, click 'Mark Inside Point'."
        )
        self.set_tutorial_text(
            "Step 2 of 3 -- mark a point inside the loop, once it's closed "
            "(right-click to finish it, if you haven't yet). Click 'Mark "
            "Inside Point', then left-click once, anywhere on the surface "
            "inside the outline you just drew. This tells the tool which "
            "side of the boundary to keep as the defect patch."
        )

    def _on_mark_seed_clicked(self):
        import slicer

        interaction_node = slicer.app.applicationLogic().GetInteractionNode()
        interaction_node.SetCurrentInteractionMode(interaction_node.ViewTransform)

        if self._seed_fiducial_node is not None:
            slicer.mrmlScene.RemoveNode(self._seed_fiducial_node)
        self._seed_fiducial_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLMarkupsFiducialNode", "ScutumSeedPoint"
        )
        self.set_absolute_point_size(self._seed_fiducial_node, config.SCUTUM_DRAW_POINT_SIZE_MM)

        selection_node = slicer.app.applicationLogic().GetSelectionNode()
        selection_node.SetActivePlaceNodeID(self._seed_fiducial_node.GetID())
        interaction_node.SetCurrentInteractionMode(interaction_node.Place)
        interaction_node.SetPlaceModePersistence(0)

        self._observer_tag = self._seed_fiducial_node.AddObserver(
            self._seed_fiducial_node.PointPositionDefinedEvent, self._on_seed_placed
        )
        self.ui.statusLabel.setText("Click once, inside the drawn outline...")

    def _on_seed_placed(self, caller, event):
        self._seed_fiducial_node.RemoveObserver(self._observer_tag)
        ras = [0.0, 0.0, 0.0]
        self._seed_fiducial_node.GetNthControlPointPositionWorld(0, ras)
        self._seed_point = tuple(ras)
        self.ui.isolateButton.setEnabled(True)
        self.ui.statusLabel.setText("Seed point marked. Click 'Isolate Patch' when ready.")
        self.set_tutorial_text(
            "Step 3 of 3 -- click 'Isolate Patch' to extract the defect "
            "region as its own mesh. If this fails with a message about too "
            "few points, the outline likely didn't fully close or the "
            "clicks landed too close together -- click 'Reset This Page' "
            "and redraw it, spacing your clicks out a bit more."
        )

    def _on_isolate_clicked(self):
        import slicer
        import trimesh

        if self._curve_node is None or self._seed_point is None:
            self.ui.statusLabel.setText("Please draw an outline and mark a seed point first.")
            return

        self.ui.statusLabel.setText("Isolating defect patch...")
        slicer.app.processEvents()

        # Use the curve's dense, interpolated surface path (not just the
        # sparse raw control points) as the loop to isolate against.
        # Consecutive clicks are typically several mesh edges apart, and
        # mesh_isolate.py bridges any gaps with mesh-graph shortest paths --
        # but on a surface with real contours, the shortest path between two
        # distant control points can cut a different route than what was
        # actually drawn. GetCurvePointsWorld() returns the same densely
        # sampled, surface-hugging path already visible in the 3D view (this
        # curve's CurveType is ShortestDistanceOnSurface), so snapping those
        # points instead makes the isolated patch's boundary track the drawn
        # shape much more closely.
        curve_points = []
        curve_points_world = self._curve_node.GetCurvePointsWorld()
        for i in range(curve_points_world.GetNumberOfPoints()):
            curve_points.append(tuple(curve_points_world.GetPoint(i)))

        mesh = trimesh.load(self.state.scutum_bone_wall_mesh_path)
        # The STL file on disk is LPS-numbered (see mesh_export.export_mesh's
        # docstring for why) -- flip back to RAS so mesh.vertices matches
        # curve_points/self._seed_point, which are always RAS from Slicer's
        # Markups nodes.
        mesh.vertices = mesh_export.flip_ras_lps_points(mesh.vertices)

        try:
            loop_indices = mesh_isolate.snap_points_to_vertices(mesh, curve_points)
            patch = mesh_isolate.isolate_surface_patch(mesh, loop_indices, self._seed_point)
        except ValueError as e:
            self.ui.statusLabel.setText(str(e))
            return

        output_path = os.path.join(
            self.state.working_dir or slicer.app.temporaryPath, "scutum_defect.stl"
        )
        # Use mesh_export.export_mesh (not patch.export directly) so the
        # RAS->LPS flip is applied consistently -- otherwise this file
        # would display mirrored when reloaded via slicer.util.loadModel.
        mesh_export.export_mesh(patch, output_path)
        self.state.scutum_defect_mesh_path = output_path

        if self.state.scutum_defect_model_node is not None:
            slicer.mrmlScene.RemoveNode(self.state.scutum_defect_model_node)
        self.state.scutum_defect_model_node = slicer.util.loadModel(output_path)

        # Hide the original (pre-isolation) bone-wall model now that the
        # isolated defect patch exists, so they don't overlap/clutter the
        # 3D view.
        if self.state.scutum_bone_wall_model_node is not None:
            self.state.scutum_bone_wall_model_node.GetDisplayNode().SetVisibility(False)

        # Hide the drawn outline curve and seed point marker now that the
        # patch has been extracted -- they've served their purpose and just
        # clutter the 3D view on top of the isolated result otherwise.
        if self._curve_node is not None:
            self._curve_node.GetDisplayNode().SetVisibility(False)
        if self._seed_fiducial_node is not None:
            self._seed_fiducial_node.GetDisplayNode().SetVisibility(False)

        self.ui.statusLabel.setText(
            f"Defect patch isolated ({len(patch.vertices)} vertices). "
            "Check it in the 3D view, then click Next."
        )

    def _on_reset_page_clicked(self):
        import slicer

        if self._curve_node is not None:
            slicer.mrmlScene.RemoveNode(self._curve_node)
        if self._seed_fiducial_node is not None:
            slicer.mrmlScene.RemoveNode(self._seed_fiducial_node)
        self._curve_node = None
        self._seed_fiducial_node = None
        self._seed_point = None

        wizard_state.clear_page_state(self.state, "scutum_draw")

        if self.state.scutum_bone_wall_model_node is not None:
            self.state.scutum_bone_wall_model_node.GetDisplayNode().SetVisibility(True)

        self.ui.markSeedButton.setEnabled(False)
        self.ui.isolateButton.setEnabled(False)
        self.ui.statusLabel.setText(
            "Outline cleared. Click 'Start Outline', then trace the defect "
            "boundary on the bone surface in the 3D view."
        )
        self.set_tutorial_text(_STEP1_TUTORIAL_TEXT)

    def _on_revert_to_here_clicked(self):
        import slicer

        if wizard_state.has_downstream_state(self.state, "scutum_draw"):
            if not slicer.util.confirmYesNoDisplay(
                "This will clear every step after this one (verification "
                "and the heatmap result). This isolated patch is kept. "
                "Continue?"
            ):
                return
            wizard_state.clear_downstream_state(self.state, "scutum_draw")
        self.ui.statusLabel.setText(
            "Later steps cleared. Go to Next when ready to redo them."
        )

    def on_leave_next(self):
        if self.state.scutum_defect_mesh_path is None:
            return False, "Please draw the defect outline and isolate the patch before continuing."
        return True, ""
