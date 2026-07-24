"""
pipeline.py
-----------
In-process port of the standalone Curvature Project v4's main.py.

End-to-end pipeline:
  1. Load scutum.stl (the defect) and pinna.stl.
  2. Compute pose-invariant curvature descriptors (extrinsic) on both.
  3. Build the defect's shape signature: curvature histograms, an
     intrinsic geodesic distance-from-center histogram (bending-invariant
     -- see geodesics.py), and boundary shape. Also measure its
     approximate geodesic center and diameter (used to size patches cut
     from the pinna).
  4. Generate candidate harvest sites spread evenly across the pinna.
  5. For each candidate: extract a geodesic patch of matching size,
     build its signature, and compute a coarse (registration-free)
     similarity score against the defect.
  6. Take the top-N coarse candidates and refine them with local ICP
     alignment, reporting Chamfer/Hausdorff distance after alignment.
  7. Propagate the coarse scores into a full per-vertex heatmap on the
     pinna. For the top sites, project the defect's *actual* shape onto
     the pinna (using the ICP transform from step 6) so the highlighted
     region matches the real footprint rather than a circular disc, then
     export everything to output_dir/output/.

This used to run as main.py in a completely separate Python process (its
own venv, because of three compiled-extension dependencies that couldn't
be trusted to install into Slicer's own bundled Python -- see this
package's __init__.py and each module's docstring for what replaced each
one). Now that every dependency is just numpy/scipy/trimesh, this runs as
a plain function call in Slicer's own process, wired up by
../../curvature_integration.py.
"""

from __future__ import annotations
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from scipy.spatial import cKDTree

from config import (
    CURVATURE_NUM_CANDIDATES,
    CURVATURE_TOP_N_REFINE,
    CURVATURE_HIST_BINS,
    CURVATURE_PATCH_RADIUS_MARGIN,
    CURVATURE_CURVEDNESS_PERCENTILE,
    CURVATURE_MEASURE_RADIUS_EDGE_MULTIPLIER,
    CURVATURE_WEIGHT_SHAPE_INDEX,
    CURVATURE_WEIGHT_CURVEDNESS,
    CURVATURE_WEIGHT_RADIAL_DISTANCE,
    CURVATURE_WEIGHT_COMPACTNESS,
    CURVATURE_WEIGHT_ASPECT_RATIO,
    CURVATURE_WEIGHT_AREA,
)

from .mesh_io import load_mesh
from .descriptors import compute_descriptors, boundary_unreliable_mask, Descriptors
from .geodesics import GeodesicPatchExtractor
from .signature import compute_signature
from .candidates import generate_candidate_vertices
from .scoring import coarse_score, ScoreWeights
from .registration import refine_candidate
from .footprint import project_defect_footprint
from .heatmap import (
    propagate_scores_to_mesh, scores_to_colors, highlight_vertices,
    boundary_vertex_local_indices, export_colored_mesh, export_ranked_candidates_csv,
)


def _noop(_line: str) -> None:
    pass


def run(scutum_path, pinna_path, output_dir,
        progress_callback: Optional[Callable[[str], None]] = None) -> dict:
    """
    Run the full curvature comparison and write results into
    `output_dir/output/`.

    Parameters
    ----------
    scutum_path, pinna_path : str or Path
        Paths to the isolated scutum defect / pinna mesh files.
    output_dir : str or Path
        Scratch folder; results are written to `output_dir/output/`,
        matching the layout the rest of the wizard (page_curvature.py)
        expects.
    progress_callback : callable(str), optional
        Called with a human-readable progress line at each major step and
        periodically during the per-candidate scoring/refinement loops,
        so a caller (e.g. Slicer's UI, via processEvents()) can show live
        progress on a run that can take a while on a dense mesh.

    Returns
    -------
    dict with keys "heatmap_path" and "csv_path" (both str).
    """
    progress = progress_callback or _noop

    output_dir = Path(output_dir)
    results_dir = output_dir / "output"
    results_dir.mkdir(parents=True, exist_ok=True)

    progress("Loading meshes...")
    scutum = load_mesh(scutum_path, name="scutum defect")
    pinna = load_mesh(pinna_path, name="pinna")
    progress(f"Loaded scutum defect: {len(scutum.vertices)} vertices, "
             f"{len(scutum.faces)} faces.")
    progress(f"Loaded pinna: {len(pinna.vertices)} vertices, "
             f"{len(pinna.faces)} faces.")

    # --- Descriptors on both meshes ---------------------------------
    progress("Computing curvature descriptors...")
    scutum_descriptors = compute_descriptors(scutum, CURVATURE_MEASURE_RADIUS_EDGE_MULTIPLIER)
    pinna_descriptors = compute_descriptors(pinna, CURVATURE_MEASURE_RADIUS_EDGE_MULTIPLIER)

    # Use the pinna's own descriptor distributions to set shared histogram
    # ranges, so the defect and every candidate patch are compared on the
    # same scale.
    curvedness_max = float(
        np.percentile(pinna_descriptors.curvedness, CURVATURE_CURVEDNESS_PERCENTILE)
    )
    curvedness_range = (0.0, max(curvedness_max, 1e-6))

    # Restricted to reliable vertices (see descriptors.boundary_unreliable_mask)
    # -- otherwise the shared range gets stretched out to the full [-1, 1]
    # by boundary-cut artifacts, wasting most of the histogram's resolution
    # on a range real, reliable curvature values rarely reach.
    reliable_pinna_si = pinna_descriptors.shape_index[pinna_descriptors.reliable]
    if len(reliable_pinna_si) == 0:
        reliable_pinna_si = pinna_descriptors.shape_index
    shape_index_range = (
        float(reliable_pinna_si.min()),
        float(reliable_pinna_si.max()),
    )

    # --- Defect signature + geodesic center/diameter -----------------
    progress("Building defect signature...")
    scutum_extractor = GeodesicPatchExtractor(scutum)
    defect_center, defect_radial_distances, defect_diameter = \
        scutum_extractor.approximate_geodesic_center()
    progress(f"Defect geodesic diameter: {defect_diameter:.3f}")

    patch_radius = 0.5 * defect_diameter * CURVATURE_PATCH_RADIUS_MARGIN
    radial_distance_range = (0.0, patch_radius)

    defect_signature = compute_signature(
        scutum, scutum_descriptors, CURVATURE_HIST_BINS,
        radial_distances=defect_radial_distances,
        radial_distance_range=radial_distance_range,
        shape_index_range=shape_index_range,
        curvedness_range=curvedness_range,
    )

    if defect_signature.boundary.hole_count > 0:
        progress(
            f"Note: defect mesh has {defect_signature.boundary.hole_count} "
            f"interior hole(s) (e.g. damage/imaging gaps). Boundary shape "
            f"(perimeter/compactness/aspect ratio) is computed from the "
            f"outer footprint only -- holes are excluded from shape matching."
        )

    # --- Candidate sites on the pinna --------------------------------
    progress("Generating candidate sites on pinna...")
    candidate_vertex_ids = generate_candidate_vertices(pinna, CURVATURE_NUM_CANDIDATES)

    # --- Coarse scoring for every candidate --------------------------
    progress("Building geodesic patch extractor for pinna "
             "(this can take a little while for dense meshes)...")
    pinna_extractor = GeodesicPatchExtractor(pinna)

    weights = ScoreWeights(
        shape_index=CURVATURE_WEIGHT_SHAPE_INDEX,
        curvedness=CURVATURE_WEIGHT_CURVEDNESS,
        radial_distance=CURVATURE_WEIGHT_RADIAL_DISTANCE,
        compactness=CURVATURE_WEIGHT_COMPACTNESS,
        aspect_ratio=CURVATURE_WEIGHT_ASPECT_RATIO,
        area=CURVATURE_WEIGHT_AREA,
    )

    progress(f"Scoring {len(candidate_vertex_ids)} candidates...")
    coarse_scores = np.full(len(candidate_vertex_ids), np.inf)
    candidate_patches = {}  # vertex_id -> trimesh patch, kept for refinement stage

    for i, vid in enumerate(candidate_vertex_ids):
        patch_mesh, patch_vertex_ids, patch_radial_distances = \
            pinna_extractor.extract_patch(vid, patch_radius)
        if patch_mesh is None or len(patch_mesh.vertices) < 10:
            continue  # patch too small/degenerate near mesh edges -- skip

        # Build descriptors for just this patch by indexing into the
        # already-computed whole-pinna descriptor arrays. The reliability
        # mask can't just be sliced the same way, though: this patch has its
        # own freshly-cut geodesic boundary that the whole-pinna mask knows
        # nothing about (it only reflects the *pinna's* own outer edge), so
        # it's recomputed fresh against patch_mesh -- cheap, since it's just
        # a boundary-edge lookup, not a curvature recompute -- and combined
        # with the sliced whole-pinna mask to also exclude vertices near the
        # pinna's real boundary.
        patch_unreliable = boundary_unreliable_mask(patch_mesh, pinna_descriptors.curvature_radius)
        patch_descriptors = Descriptors(
            shape_index=pinna_descriptors.shape_index[patch_vertex_ids],
            curvedness=pinna_descriptors.curvedness[patch_vertex_ids],
            reliable=(~patch_unreliable) & pinna_descriptors.reliable[patch_vertex_ids],
            curvature_radius=pinna_descriptors.curvature_radius,
        )

        patch_signature = compute_signature(
            patch_mesh, patch_descriptors, CURVATURE_HIST_BINS,
            radial_distances=patch_radial_distances,
            radial_distance_range=radial_distance_range,
            shape_index_range=shape_index_range,
            curvedness_range=curvedness_range,
        )

        coarse_scores[i] = coarse_score(defect_signature, patch_signature, weights)
        candidate_patches[vid] = patch_mesh

        if (i + 1) % 25 == 0 or (i + 1) == len(candidate_vertex_ids):
            progress(f"  scored {i + 1}/{len(candidate_vertex_ids)} candidates")

    valid_mask = np.isfinite(coarse_scores)
    if valid_mask.sum() == 0:
        raise RuntimeError(
            "No valid candidate patches were extracted. Try increasing "
            "the candidate count in config.py, or check that the pinna/"
            "scutum meshes loaded correctly."
        )

    candidate_vertex_ids = candidate_vertex_ids[valid_mask]
    coarse_scores = coarse_scores[valid_mask]

    # --- Refine top-N with local ICP alignment -----------------------
    order = np.argsort(coarse_scores)  # ascending: best (lowest) first
    top_n = min(CURVATURE_TOP_N_REFINE, len(order))
    top_indices = order[:top_n]

    progress(f"Refining top {top_n} candidates with local ICP alignment...")
    refined_vertex_ids = []
    refined_chamfer = []
    refined_hausdorff = []
    refined_coarse = []
    transform_by_vid = {}  # vertex_id -> 4x4 transform (patch frame -> defect frame)

    for rank, idx in enumerate(top_indices, start=1):
        vid = candidate_vertex_ids[idx]
        patch_mesh = candidate_patches[vid]

        result = refine_candidate(patch_mesh, scutum)

        refined_vertex_ids.append(vid)
        refined_chamfer.append(result.chamfer_distance)
        refined_hausdorff.append(result.hausdorff_distance)
        refined_coarse.append(coarse_scores[idx])
        transform_by_vid[vid] = result.transform

        progress(f"  [{rank}/{top_n}] vertex {vid}: "
                 f"coarse={coarse_scores[idx]:.4f}  "
                 f"chamfer={result.chamfer_distance:.4f}  "
                 f"hausdorff={result.hausdorff_distance:.4f}  "
                 f"fitness={result.fitness:.3f}")

    # Re-rank the refined shortlist by Chamfer distance (more precise
    # than the coarse score, now that we've actually aligned the patches).
    refined_vertex_ids = np.array(refined_vertex_ids)
    refined_chamfer = np.array(refined_chamfer)
    refined_hausdorff = np.array(refined_hausdorff)
    refined_coarse = np.array(refined_coarse)

    refine_order = np.argsort(refined_chamfer)
    refined_vertex_ids = refined_vertex_ids[refine_order]
    refined_chamfer = refined_chamfer[refine_order]
    refined_hausdorff = refined_hausdorff[refine_order]
    refined_coarse = refined_coarse[refine_order]

    # --- Build full heatmap + export ---------------------------------
    progress("Building full-mesh heatmap...")
    vertex_scores = propagate_scores_to_mesh(pinna, candidate_vertex_ids, coarse_scores)
    colors = scores_to_colors(vertex_scores)

    # For the top sites, highlight the defect's *actual* shape rather
    # than the circular geodesic disc used for coarse screening/alignment.
    top_3_center_ids = refined_vertex_ids[:min(3, len(refined_vertex_ids))]

    if len(top_3_center_ids) > 0:
        defect_boundary_local_ids = boundary_vertex_local_indices(scutum)
        pinna_tree = cKDTree(pinna.vertices)

        fill_ids_list = []
        outline_ids_list = []
        for vid in top_3_center_ids:
            fill_ids, outline_ids = project_defect_footprint(
                scutum, defect_boundary_local_ids,
                transform_by_vid[vid], pinna_tree,
            )
            fill_ids_list.append(fill_ids)
            outline_ids_list.append(outline_ids)

        top_3_patch_vertex_ids = np.unique(np.concatenate(fill_ids_list))
        # Outline each site individually (not the merged union) so that
        # if two of the top sites overlap, each one's boundary is still
        # visible on top of the shared blue fill.
        boundary_ids = np.unique(np.concatenate(outline_ids_list))
    else:
        top_3_patch_vertex_ids = np.array([], dtype=int)
        boundary_ids = np.array([], dtype=int)

    colors = highlight_vertices(colors, top_3_patch_vertex_ids)
    colors = highlight_vertices(colors, boundary_ids, highlight_rgb=(0, 0, 0))

    heatmap_path = results_dir / "pinna_heatmap.ply"
    export_colored_mesh(pinna, colors, heatmap_path)

    csv_path = results_dir / "top_harvest_sites.csv"
    export_ranked_candidates_csv(
        pinna, refined_vertex_ids,
        scores={
            "coarse_score": refined_coarse,
            "chamfer_distance": refined_chamfer,
            "hausdorff_distance": refined_hausdorff,
        },
        out_path=csv_path,
    )

    progress("Done.")
    progress(f"Best {len(top_3_center_ids)} site(s) highlighted in blue "
             f"with a black outline, shaped to match the defect's actual "
             f"footprint (projected via the ICP alignment), not a circle.")

    return {"heatmap_path": str(heatmap_path), "csv_path": str(csv_path)}
