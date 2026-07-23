"""
page_pinna_draw.py
=====================
Page 7: the surgeon draws a closed outline around the pinna on the 3D
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
(from the scutum stage) as the cut-plane position wasn't reliable -- it was
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
  - startCurveButton        (QPushButton)
  - markSeedButton          (QPushButton)
  - markCanalOpeningButton  (QPushButton)
  - isolateButton           (QPushButton)
  - statusLabel             (QLabel)
"""

from __future__ import annotations
import os
import numpy as np
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from core import mesh_isolate, mesh_export


class PinnaDrawPage(WizardPage):
    def on_enter(self):
        self._curve_node = None
        self._seed_fiducial_node = None
        self._seed_point = None
        self._canal_fiducial_node = None
        self._canal_point = None

        self.ui.startCurveButton.clicked.connect(self._on_start_curve_clicked)
        self.ui.markSeedButton.clicked.connect(self._on_mark_seed_clicked)
        self.ui.markCanalOpeningButton.clicked.connect(self._on_mark_canal_clicked)
        self.ui.isolateButton.clicked.connect(self._on_isolate_clicked)
        self.ui.markSeedButton.setEnabled(False)
        self.ui.markCanalOpeningButton.setEnabled(False)
        self.ui.isolateButton.setEnabled(False)
        self.ui.statusLabel.setText(
            "Click 'Start Outline', then trace around the pinna on the skin "
            "surface in the 3D view. Click near your starting point to close it."
        )

    def _on_start_curve_clicked(self):
        import slicer

        if self._curve_node is not None:
            slicer.mrmlScene.RemoveNode(self._curve_node)

        self._curve_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLMarkupsClosedCurveNode", "PinnaOutline"
        )
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
            "Trace around the pinna by clicking points on the surface. When "
            "you're done, click 'Mark Inside Point' and 'Mark Canal Opening'."
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

    def _on_mark_canal_clicked(self):
        import slicer

        interaction_node = slicer.app.applicationLogic().GetInteractionNode()
        interaction_node.SetCurrentInteractionMode(interaction_node.ViewTransform)

        if self._canal_fiducial_node is not None:
            slicer.mrmlScene.RemoveNode(self._canal_fiducial_node)
        self._canal_fiducial_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLMarkupsFiducialNode", "PinnaCanalOpeningMarker"
        )

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

    def _update_isolate_enabled(self):
        self.ui.isolateButton.setEnabled(
            self._seed_point is not None and self._canal_point is not None
        )

    def _on_isolate_clicked(self):
        import slicer
        import trimesh

        if self._curve_node is None or self._seed_point is None or self._canal_point is None:
            self.ui.statusLabel.setText(
                "Please draw an outline, mark a seed point, and mark the canal "
                "opening first."
            )
            return

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

        mesh = trimesh.load(self.state.pinna_region_mesh_path)
        # The STL file on disk is LPS-numbered (see mesh_export.export_mesh's
        # docstring for why) -- flip back to RAS so mesh.vertices matches
        # curve_points/self._seed_point/self._canal_point, which are always
        # RAS from Slicer's Markups nodes.
        mesh.vertices = mesh_export.flip_ras_lps_points(mesh.vertices)

        try:
            loop_indices = mesh_isolate.snap_points_to_vertices(mesh, curve_points)
            patch = mesh_isolate.isolate_surface_patch(mesh, loop_indices, self._seed_point)

            # Crop away anything toward the head's interior from the
            # surgeon's own canal-opening marker on this mesh, using the
            # ear canal's landmarks (from the earlier scutum stage) only
            # for the "which way is inward" direction.
            canal_landmarks = self.state.scutum_landmarks
            if canal_landmarks.canal_opening is not None and canal_landmarks.near_eardrum is not None:
                axis_direction = np.array(canal_landmarks.near_eardrum) - np.array(
                    canal_landmarks.canal_opening
                )
                patch = mesh_isolate.crop_toward_canal(patch, self._canal_point, axis_direction)
                # The crop can leave severed head-interior material as a
                # disconnected island rather than removing it in one
                # clean cut -- keep only the piece still connected to the
                # surgeon's own seed point.
                patch = mesh_isolate.keep_connected_component_containing(
                    patch, self._seed_point
                )
        except ValueError as e:
            self.ui.statusLabel.setText(str(e))
            return

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

    def on_leave_next(self):
        if self.state.pinna_isolated_mesh_path is None:
            return False, "Please draw the pinna outline and isolate the patch before continuing."
        return True, ""
