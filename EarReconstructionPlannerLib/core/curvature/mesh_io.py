"""
mesh_io.py
----------
Small helper for loading STL files as clean trimesh.Trimesh objects.

Both the scutum defect and the pinna are open surfaces (single-sided
shells), not watertight solids, so we don't try to force them into
solids here -- we just clean up common STL export issues.

Ported verbatim from the standalone Curvature Project v4 (this file never
touched any of the three dependencies that forced the old subprocess
architecture).
"""

from __future__ import annotations
from pathlib import Path
import trimesh


def load_mesh(path: str | Path, name: str = "mesh") -> trimesh.Trimesh:
    """
    Load an STL file and return a cleaned trimesh.Trimesh.

    Parameters
    ----------
    path : str or Path
        Path to the .stl file.
    name : str
        Human-readable name, only used for error messages / logging.

    Returns
    -------
    trimesh.Trimesh
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Could not find {name} at '{path}'. "
            f"Make sure the file exists."
        )

    loaded = trimesh.load(path, process=True)

    # Some STL exporters produce a Scene instead of a single Trimesh
    # (e.g. if there are multiple disconnected shells stored as separate
    # geometries). We merge everything into one mesh for simplicity.
    if isinstance(loaded, trimesh.Scene):
        geometries = list(loaded.geometry.values())
        if len(geometries) == 0:
            raise ValueError(f"{name}: loaded file contains no geometry.")
        mesh = trimesh.util.concatenate(geometries)
    else:
        mesh = loaded

    # Basic cleanup: merge duplicate vertices, remove degenerate/duplicate
    # faces, and make sure normals are consistently oriented.
    mask = mesh.unique_faces() & mesh.nondegenerate_faces()
    mesh.update_faces(mask)
    mesh.fix_normals()
    mesh.merge_vertices()

    if hasattr(mesh, "remove_unreferenced_vertices"):
        mesh.remove_unreferenced_vertices()

    if mesh.vertices.shape[0] == 0 or mesh.faces.shape[0] == 0:
        raise ValueError(f"{name}: mesh is empty after cleanup.")

    return mesh
