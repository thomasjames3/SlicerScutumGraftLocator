"""
curvature_integration.py
===========================
Bridges this extension to Curvature Project v4.

IMPORTANT ARCHITECTURE NOTE: this deliberately does NOT try to import
Curvature Project v4's code directly into Slicer's Python process.
Curvature Project v4 runs in its own Python 3.12.10 virtual environment
with compiled-extension dependencies (pymeshlab, potpourri3d, open3d).
Slicer ships its own embedded Python, which is very likely a different
minor version -- and compiled-extension wheels are built for a specific
Python version, so they may simply fail to install into Slicer's Python
even if `pip install` is attempted there. Rather than fight that, this
module runs Curvature Project v4 as a completely separate process, using
its own existing venv's Python interpreter, and communicates with it only
through files on disk.

HOW THIS MATCHES main.py's ACTUAL INTERFACE (confirmed from the real file):
main.py takes no command-line arguments at all. It reads
`Path("data") / "scutum.stl"` and `Path("data") / "pinna.stl"` relative to
wherever it's *run from* (its process working directory, not its own file
location), and writes `Path("output") / "pinna_heatmap.ply"` and
`Path("output") / "top_harvest_sites.csv"` the same way. Its own imports
(`from src.mesh_io import ...`) resolve based on main.py's own file
location, which Python adds to sys.path automatically -- so none of that
is affected by which directory we run it from.

This means the cleanest, least invasive integration is: create a fresh
scratch folder per case, copy our two isolated meshes into
scratch/data/scutum.stl and scratch/data/pinna.stl, run main.py with that
scratch folder as the subprocess's working directory, and read the results
back from scratch/output/. Nothing about Curvature Project v4 itself needs
to change.
"""

from __future__ import annotations
import os
import shutil
import subprocess
import logging

logger = logging.getLogger(__name__)

# TODO: update these two paths to match your machine, e.g. on Windows:
#   VENV_PYTHON_PATH = r"C:\Users\Thomas James\Documents\Curvature Project v4\.venv\Scripts\python.exe"
#   CURVATURE_PROJECT_MAIN_PATH = r"C:\Users\Thomas James\Documents\Curvature Project v4\main.py"
VENV_PYTHON_PATH = r"C:\Users\Thomas James\Documents\Curvature Project v4\.venv\Scripts\python.exe"
CURVATURE_PROJECT_MAIN_PATH = r"C:\Users\Thomas James\Documents\Curvature Project v4\main.py"


def is_configured() -> bool:
    """Returns True if both the venv interpreter and main.py actually exist
    at the configured paths -- lets the UI show a clear 'not set up yet'
    message instead of a confusing subprocess error."""
    return os.path.isfile(VENV_PYTHON_PATH) and os.path.isfile(CURVATURE_PROJECT_MAIN_PATH)


def run_curvature_comparison(
    pinna_mesh_path: str,
    scutum_defect_mesh_path: str,
    output_dir: str,
) -> str:
    """
    Runs Curvature Project v4 as a subprocess in its own venv, comparing
    the isolated pinna mesh against the scutum defect mesh, and returns
    the path to the resulting heatmap (.ply).

    Parameters
    ----------
    pinna_mesh_path : str
        Path to the isolated pinna STL (state.pinna_isolated_mesh_path).
    scutum_defect_mesh_path : str
        Path to the isolated scutum defect STL (state.scutum_defect_mesh_path).
    output_dir : str
        A per-case scratch folder. A `data/` subfolder (containing the two
        renamed mesh copies main.py expects) and an `output/` subfolder
        (where main.py writes its results) are created inside this.

    Returns
    -------
    str
        Path to the generated pinna_heatmap.ply file.

    Raises
    ------
    NotImplementedError
        If VENV_PYTHON_PATH / CURVATURE_PROJECT_MAIN_PATH haven't been set
        to real paths yet.
    RuntimeError
        If the subprocess runs but exits with an error, or doesn't produce
        the expected output file -- the underlying stderr is included so
        the surgeon (or you, during testing) can see exactly what
        Curvature Project v4 reported.
    """
    if not is_configured():
        raise NotImplementedError(
            "Curvature Project v4 isn't wired up yet -- VENV_PYTHON_PATH and/or "
            "CURVATURE_PROJECT_MAIN_PATH in curvature_integration.py don't point "
            "to real files on this machine. Update those two paths at the top "
            "of this file."
        )

    data_dir = os.path.join(output_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    # main.py expects these exact filenames, relative to wherever it's run
    # from -- see module docstring.
    shutil.copyfile(scutum_defect_mesh_path, os.path.join(data_dir, "scutum.stl"))
    shutil.copyfile(pinna_mesh_path, os.path.join(data_dir, "pinna.stl"))

    command = [VENV_PYTHON_PATH, CURVATURE_PROJECT_MAIN_PATH]
    logger.info("Running Curvature Project v4 in %s: %s", output_dir, " ".join(command))

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=output_dir,  # so main.py's Path("data")/Path("output") resolve here
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Curvature Project v4 exited with an error:\n" + result.stderr
        )

    heatmap_path = os.path.join(output_dir, "output", "pinna_heatmap.ply")
    if not os.path.isfile(heatmap_path):
        raise RuntimeError(
            "Curvature Project v4 finished without error, but the expected "
            f"output file wasn't found at {heatmap_path}. Its printed output "
            f"was:\n{result.stdout}"
        )

    return heatmap_path


def get_ranked_candidates_csv_path(output_dir: str) -> str:
    """
    Convenience helper: main.py also writes a ranked shortlist of harvest
    sites (vertex ids, coarse score, Chamfer/Hausdorff distance after ICP)
    to output/top_harvest_sites.csv, alongside the heatmap. Returns that
    path (same output_dir passed to run_curvature_comparison), in case the
    wizard wants to show or open it for the surgeon.
    """
    return os.path.join(output_dir, "output", "top_harvest_sites.csv")
