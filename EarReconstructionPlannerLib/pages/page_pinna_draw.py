"""
page_pinna_draw.py
=====================
Page 7: the surgeon draws a closed outline around the pinna on the 3D
skin-surface mesh; the enclosed patch is extracted as the isolated pinna
mesh for Curvature Project v4.

Structurally identical to page_scutum_draw.py -- same surface-constrained
curve + seed point + core/mesh_isolate.py extraction. Kept as a separate
page controller (rather than sharing one class) since the two stages
operate on different state fields and different meshes, but the underlying
mechanism is the same by design.

Expected widgets in page_pinna_draw.ui:
  - instructionLabel    (QLabel)
  - startCurveButton    (QPushButton)
  - markSeedButton      (QPushButton)
  - isolateButton       (QPushButton)
  - statusLabel         (QLabel)
"""

from __future__ import annotations
import os
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from core import mesh_isolate


class PinnaDrawPage(WizardPage):
    def on_enter(self):
        self._curve_node = None
        self._seed_fiducial_node = None
        self._seed_point = None

        self.ui.startCurveButton.clicked.connect(self._on_start_curve_clicked)
        self.ui.markSeedButton.clicked.connect(self._on_mark_seed_clicked)
        self.ui.isolateButton.clicked.connect(self._on_isolate_clicked)
        self.ui.markSeedButton.setEnabled(False)
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
            self._curve_node.SetAndObserveSurfaceConstraintNode(
                self.state.pinna_region_model_node
            )

        interaction_node = slicer.app.applicationLogic().GetInteractionNode()
        selection_node = slicer.app.applicationLogic().GetSelectionNode()
        selection_node.SetActivePlaceNodeID(self._curve_node.GetID())
        interaction_node.SetCurrentInteractionMode(interaction_node.Place)
        interaction_node.SetPlaceModePersistence(1)

        self.ui.markSeedButton.setEnabled(True)
        self.ui.statusLabel.setText(
            "Trace around the pinna by clicking points on the surface. When "
            "you're done, click 'Mark Inside Point'."
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

        self._observer_tag = self._seed_fiducial_node.AddObserver(
            self._seed_fiducial_node.PointPositionDefinedEvent, self._on_seed_placed
        )
        self.ui.statusLabel.setText("Click once, inside the pinna outline...")

    def _on_seed_placed(self, caller, event):
        self._seed_fiducial_node.RemoveObserver(self._observer_tag)
        ras = [0.0, 0.0, 0.0]
        self._seed_fiducial_node.GetNthControlPointPositionWorld(0, ras)
        self._seed_point = tuple(ras)
        self.ui.isolateButton.setEnabled(True)
        self.ui.statusLabel.setText("Seed point marked. Click 'Isolate Patch' when ready.")

    def _on_isolate_clicked(self):
        import slicer
        import trimesh

        if self._curve_node is None or self._seed_point is None:
            self.ui.statusLabel.setText("Please draw an outline and mark a seed point first.")
            return

        self.ui.statusLabel.setText("Isolating pinna patch...")
        slicer.app.processEvents()

        curve_points = []
        n = self._curve_node.GetNumberOfControlPoints()
        for i in range(n):
            ras = [0.0, 0.0, 0.0]
            self._curve_node.GetNthControlPointPositionWorld(i, ras)
            curve_points.append(tuple(ras))

        mesh = trimesh.load(self.state.pinna_region_mesh_path)

        try:
            loop_indices = mesh_isolate.snap_points_to_vertices(mesh, curve_points)
            patch = mesh_isolate.isolate_surface_patch(mesh, loop_indices, self._seed_point)
        except ValueError as e:
            self.ui.statusLabel.setText(str(e))
            return

        output_path = os.path.join(
            self.state.working_dir or slicer.app.temporaryPath, "pinna_isolated.stl"
        )
        patch.export(output_path)
        self.state.pinna_isolated_mesh_path = output_path

        slicer.util.loadModel(output_path)
        self.ui.statusLabel.setText(
            f"Pinna isolated ({len(patch.vertices)} vertices). "
            "Check it in the 3D view, then click Next."
        )

    def on_leave_next(self):
        if self.state.pinna_isolated_mesh_path is None:
            return False, "Please draw the pinna outline and isolate the patch before continuing."
        return True, ""
