"""
heatmap.py
----------
Turns a sparse set of candidate scores into a full per-vertex heatmap
across the pinna, colors the mesh accordingly, and exports it (plus a
CSV of the best sites) for review.

STL doesn't support vertex colors, so the heatmap mesh is exported as
.ply, which Slicer's model loader can read directly (see page_curvature.py).

The standalone Curvature Project v4 used matplotlib's 'RdYlGn' colormap
for `scores_to_colors()`. matplotlib is a large dependency compared to
what this stage actually needs (a 3-stop diverging colormap), so it's
replaced here with a hand-rolled interpolation using the same colors
matplotlib's own 'RdYlGn' is built from (ColorBrewer's RdYlGn palette),
so the heatmap looks the same without pulling in the extra dependency.
Nothing else in this file ever depended on matplotlib, open3d,
pymeshlab, or potpourri3d.
"""

from pathlib import Path
import csv

import numpy as np
import trimesh
from scipy.spatial import cKDTree

# ColorBrewer's 3-class RdYlGn stops -- the same palette matplotlib's own
# 'RdYlGn' colormap is built from -- so the heatmap reads the same way
# (red = poor match, yellow = middling, green = good match) without
# needing matplotlib installed.
_RED = np.array([215, 48, 39], dtype=float)
_YELLOW = np.array([255, 255, 191], dtype=float)
_GREEN = np.array([26, 152, 80], dtype=float)


def propagate_scores_to_mesh(mesh: trimesh.Trimesh,
                              candidate_vertex_ids: np.ndarray,
                              candidate_scores: np.ndarray,
                              smoothing_iters: int = 3) -> np.ndarray:
    """
    Assign every vertex on `mesh` the score of its nearest candidate
    (by 3D position), then smooth the result over the mesh's own
    connectivity so the heatmap reads as a continuous gradient rather
    than a patchwork of flat regions.

    Lower score = better match (consistent with coarse_score / refined
    Chamfer distance), so smaller values should map to the "hot"/best end
    of whatever colormap you use downstream.
    """
    candidate_points = mesh.vertices[candidate_vertex_ids]
    tree = cKDTree(candidate_points)
    _, nearest = tree.query(mesh.vertices)
    vertex_scores = candidate_scores[nearest].astype(np.float64)

    neighbors = mesh.vertex_neighbors  # list of arrays, one per vertex
    for _ in range(smoothing_iters):
        smoothed = vertex_scores.copy()
        for i, nbrs in enumerate(neighbors):
            if len(nbrs) == 0:
                continue
            smoothed[i] = 0.5 * vertex_scores[i] + 0.5 * vertex_scores[nbrs].mean()
        vertex_scores = smoothed

    return vertex_scores


def scores_to_colors(vertex_scores: np.ndarray) -> np.ndarray:
    """
    Map per-vertex scores to RGBA colors, where the *lowest* scores
    (best matches) get the "good" (green) end of the scale: green = good
    match, red = poor match, yellow = in between.
    """
    inverted = -vertex_scores  # so low score -> high value -> "good" color
    vmin, vmax = float(inverted.min()), float(inverted.max())
    if vmax - vmin < 1e-12:
        normalized = np.full_like(inverted, 0.5)
    else:
        normalized = (inverted - vmin) / (vmax - vmin)

    colors = np.empty((len(normalized), 4), dtype=np.uint8)

    lower_half = normalized <= 0.5
    t_lower = np.clip(normalized[lower_half] / 0.5, 0.0, 1.0)[:, None]
    colors[lower_half, :3] = np.round(_RED + t_lower * (_YELLOW - _RED)).astype(np.uint8)

    upper_half = ~lower_half
    t_upper = np.clip((normalized[upper_half] - 0.5) / 0.5, 0.0, 1.0)[:, None]
    colors[upper_half, :3] = np.round(_YELLOW + t_upper * (_GREEN - _YELLOW)).astype(np.uint8)

    colors[:, 3] = 255
    return colors


def boundary_vertex_local_indices(mesh: trimesh.Trimesh) -> np.ndarray:
    """
    Find the boundary (outline) vertices of a mesh, as *local* indices
    into that mesh's own vertex array.

    A boundary edge is one used by exactly one face (an "interior" edge
    of a manifold-ish patch is shared by two faces). We find those via
    trimesh's row-grouping utility rather than mesh.outline(), since it
    doesn't require the boundary to form a single closed loop -- useful
    near irregular mesh edges.
    """
    from trimesh.grouping import group_rows

    boundary_edge_rows = group_rows(mesh.edges_sorted, require_count=1)
    if len(boundary_edge_rows) == 0:
        return np.array([], dtype=int)

    boundary_edges = mesh.edges_sorted[boundary_edge_rows]
    return np.unique(boundary_edges)


def highlight_vertices(colors: np.ndarray, vertex_ids: np.ndarray,
                        highlight_rgb=(100, 180, 255)) -> np.ndarray:
    """Overwrite specific vertices with a distinct highlight color (in place-safe copy).

    Default is a light, saturated blue, chosen to stand out clearly
    against the red/yellow/green heatmap while still leaving a black
    outline (see boundary_vertex_local_indices) visible on top of it.
    """
    colors = colors.copy()
    colors[vertex_ids, 0] = highlight_rgb[0]
    colors[vertex_ids, 1] = highlight_rgb[1]
    colors[vertex_ids, 2] = highlight_rgb[2]
    colors[vertex_ids, 3] = 255
    return colors


def export_colored_mesh(mesh: trimesh.Trimesh, colors: np.ndarray, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    colored = mesh.copy()
    colored.visual.vertex_colors = colors
    colored.export(out_path)


def export_ranked_candidates_csv(mesh: trimesh.Trimesh,
                                  vertex_ids: np.ndarray,
                                  scores: dict,
                                  out_path):
    """
    `scores` is a dict of {column_name: values_array_aligned_with_vertex_ids},
    e.g. {"coarse_score": ..., "chamfer_distance": ..., "hausdorff_distance": ...}
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["rank", "vertex_id", "x", "y", "z"] + list(scores.keys())
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rank, vid in enumerate(vertex_ids, start=1):
            row = {
                "rank": rank,
                "vertex_id": int(vid),
                "x": mesh.vertices[vid, 0],
                "y": mesh.vertices[vid, 1],
                "z": mesh.vertices[vid, 2],
            }
            for key, values in scores.items():
                row[key] = values[rank - 1]
            writer.writerow(row)
