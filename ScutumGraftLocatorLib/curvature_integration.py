"""
curvature_integration.py
===========================
Bridges this extension's wizard to the curvature comparison pipeline.

FORMER ARCHITECTURE (kept here for context, no longer true): this used to
run Curvature Project v4 as a completely separate subprocess, in its own
Python 3.12 venv, because three of its dependencies (pymeshlab,
potpourri3d, open3d) are compiled extensions that couldn't be trusted to
install into Slicer's own bundled Python. That meant a surgeon installing
this extension also needed a separate Python install and that whole
side-project checked out next to it -- a real violation of this project's
"zero Python experience required" constraint.

CURRENT ARCHITECTURE: the comparison has been ported to
`core/curvature/` (see that package's __init__.py and each module's
docstring for what replaced pymeshlab/potpourri3d/open3d), which only
needs numpy/scipy/trimesh -- already required and already installed by
this extension's own Setup page (dependencies.py) for the segmentation
pipeline. So this module now just calls `core.curvature.pipeline.run()`
directly, in-process, with no subprocess, no venv, and no extra install.
Everything below this point in the module docstring reflects that.

This module still has no Slicer/Qt dependency itself -- `progress_callback`
is a plain function; page_curvature.py is the only place that knows about
Qt (it uses the callback to call `slicer.app.processEvents()` so the UI
stays responsive during a run that can take a while on a dense mesh).
"""

from __future__ import annotations
import csv
import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def describe_configuration_problem() -> str:
    """Returns "" if the curvature comparison pipeline is ready to run,
    otherwise a plain-language explanation of what's missing. In practice
    this should basically always be "" by the time a surgeon reaches this
    page, since the Setup page already installs everything
    core/curvature/ needs -- this exists as a defense-in-depth check
    (e.g. if a package was somehow uninstalled after Setup ran) rather
    than a real "not configured yet" state like the old subprocess/venv
    version of this check used to guard against."""
    try:
        import core.curvature.pipeline  # noqa: F401
    except ImportError as e:
        return (
            f"The curvature comparison couldn't be loaded ({e}). This "
            "usually means a required package isn't installed yet -- go "
            "back to the Setup page and click Install."
        )
    return ""


def is_configured() -> bool:
    """Returns True if the curvature comparison pipeline is ready to run."""
    return describe_configuration_problem() == ""


def run_curvature_comparison(
    pinna_mesh_path: str,
    scutum_defect_mesh_path: str,
    output_dir: str,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Runs the curvature comparison in-process, comparing the isolated
    pinna mesh against the scutum defect mesh, and returns the path to
    the resulting heatmap (.ply).

    Parameters
    ----------
    pinna_mesh_path : str
        Path to the isolated pinna STL (state.pinna_isolated_mesh_path).
    scutum_defect_mesh_path : str
        Path to the isolated scutum defect STL (state.scutum_defect_mesh_path).
    output_dir : str
        A per-case scratch folder. Results are written to
        `output_dir/output/` (pinna_heatmap.ply, top_harvest_sites.csv).
    progress_callback : callable, optional
        Called with a human-readable progress line at each major step of
        the comparison (e.g. "scored 150/300 candidates..."), so a
        Qt-based caller can stream it into a text box and pump its own
        event loop (`slicer.app.processEvents()`) to stay responsive.

    Returns
    -------
    str
        Path to the generated pinna_heatmap.ply file.

    Raises
    ------
    NotImplementedError
        If the curvature comparison pipeline isn't ready to run -- see
        describe_configuration_problem() for specifics.
    """
    problem = describe_configuration_problem()
    if problem:
        raise NotImplementedError(problem)

    from core.curvature import pipeline as curvature_pipeline

    result = curvature_pipeline.run(
        scutum_path=scutum_defect_mesh_path,
        pinna_path=pinna_mesh_path,
        output_dir=output_dir,
        progress_callback=progress_callback,
    )
    return result["heatmap_path"]


def get_ranked_candidates_csv_path(output_dir: str) -> str:
    """
    Convenience helper: the comparison also writes a ranked shortlist of
    harvest sites (vertex ids, coarse score, Chamfer/Hausdorff distance
    after ICP) to output/top_harvest_sites.csv, alongside the heatmap.
    Returns that path (same output_dir passed to run_curvature_comparison).
    """
    return str(Path(output_dir) / "output" / "top_harvest_sites.csv")


def read_ranked_candidates(csv_path: str) -> list[dict]:
    """Reads top_harvest_sites.csv back into a list of plain dicts (string
    values, as written) for display in the wizard -- e.g.
    [{"rank": "1", "vertex_id": "708", "x": "...", ..., "coarse_score": "...",
    "chamfer_distance": "...", "hausdorff_distance": "..."}, ...].
    Returns an empty list if the file doesn't exist (e.g. called before a
    successful run)."""
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        return []
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))
