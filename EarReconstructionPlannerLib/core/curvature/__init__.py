"""
core.curvature package: in-process port of the standalone Curvature Project
v4 (curvature-based scutum-defect-to-pinna harvest site comparison).

Ported so the whole extension can run on Slicer's own bundled Python, with
no separate Python install, venv, or subprocess -- the three dependencies
that originally forced a subprocess bridge (pymeshlab, potpourri3d, open3d)
are each replaced here with equivalents built on numpy/scipy/trimesh, which
this extension already requires for its segmentation pipeline (see
../../dependencies.py). See pipeline.py for the end-to-end entry point, and
each module's docstring for what it replaces and why.

Like core/ itself, nothing in this subpackage knows about Slicer/Qt -- every
function takes/returns plain numpy arrays or trimesh.Trimesh objects, so it's
testable standalone.
"""
