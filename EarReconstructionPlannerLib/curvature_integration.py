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

WHERE CURVATURE PROJECT V4 LIVES: it's now checked into this same repo, as
a sibling of EarReconstructionPlannerLib/ (see `Curvature Project v4/` at
the repo root). Rather than hardcoding an absolute, machine-specific path
(the original placeholder pointed at a `Documents` folder that doesn't
exist on this machine), the path is derived relative to this file, so it
keeps working regardless of whose machine/checkout this is, as long as the
two projects stay side by side.
"""

from __future__ import annotations
import csv
import logging
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import config

logger = logging.getLogger(__name__)

# Curvature Project v4's own directory, located relative to this file
# (EarReconstructionPlannerLib/curvature_integration.py -> repo root ->
# "Curvature Project v4"). See module docstring for why this isn't a
# hardcoded absolute path.
CURVATURE_PROJECT_DIR = Path(__file__).resolve().parent.parent / "Curvature Project v4"

# The venv's own interpreter is used (not Slicer's), since that's where
# Curvature Project v4's compiled-extension dependencies are actually
# installed. Windows layout is checked first (this project is developed
# and used on Windows), with a posix fallback in case the checkout is ever
# moved to Mac/Linux and the venv is recreated there.
_VENV_PYTHON_CANDIDATES = (
    CURVATURE_PROJECT_DIR / ".venv" / "Scripts" / "python.exe",  # Windows
    CURVATURE_PROJECT_DIR / ".venv" / "bin" / "python",  # macOS/Linux
)
VENV_PYTHON_PATH = next(
    (p for p in _VENV_PYTHON_CANDIDATES if p.is_file()), _VENV_PYTHON_CANDIDATES[0]
)

CURVATURE_PROJECT_MAIN_PATH = CURVATURE_PROJECT_DIR / "main.py"


def describe_configuration_problem() -> str:
    """Returns "" if Curvature Project v4 is fully set up and ready to run,
    otherwise a plain-language explanation of exactly what's missing --
    lets the UI show something more useful than a generic "not connected"
    message, and gives a concrete path to check when troubleshooting."""
    if not CURVATURE_PROJECT_DIR.is_dir():
        return (
            "Curvature Project v4 wasn't found -- expected it at "
            f"\"{CURVATURE_PROJECT_DIR}\", alongside this extension. Make "
            "sure that folder exists there."
        )
    if not VENV_PYTHON_PATH.is_file():
        return (
            "Curvature Project v4 was found, but its Python virtual "
            f"environment wasn't -- expected \"{VENV_PYTHON_PATH}\". Set up "
            "its venv first (see that project's own README)."
        )
    if not CURVATURE_PROJECT_MAIN_PATH.is_file():
        return (
            "Curvature Project v4's main.py wasn't found at "
            f"\"{CURVATURE_PROJECT_MAIN_PATH}\"."
        )
    return ""


def is_configured() -> bool:
    """Returns True if the venv interpreter and main.py actually exist at
    the expected paths -- lets the UI show a clear 'not set up yet'
    message instead of a confusing subprocess error."""
    return describe_configuration_problem() == ""


def _stream_pipe_to_queue(pipe, line_queue: "queue.Queue"):
    """Runs in a background thread: forwards each line the subprocess
    prints to line_queue as it arrives, then pushes a `None` sentinel once
    the subprocess closes its output. Reading a pipe like this off the main
    thread is what lets the caller keep polling (and pumping its own UI
    event loop) instead of blocking until the whole subprocess finishes."""
    try:
        for line in iter(pipe.readline, ""):
            line_queue.put(line)
    finally:
        pipe.close()
        line_queue.put(None)


def run_curvature_comparison(
    pinna_mesh_path: str,
    scutum_defect_mesh_path: str,
    output_dir: str,
    progress_callback: Optional[Callable[[str], None]] = None,
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
        (where main.py writes its results) are created inside this. Any
        contents left over from a previous run in this folder are removed
        first -- see the note below on why that matters.
    progress_callback : callable, optional
        Called with each line of the subprocess's console output
        (progress messages like "scored 150/300 candidates...") as soon as
        it's printed, with the trailing newline stripped. Also called with
        an empty string roughly every 0.2s while the subprocess is running
        but has printed nothing new -- a plain heartbeat tick a Qt-based
        caller can use to pump its own event loop (`slicer.app.processEvents()`)
        so the UI doesn't look frozen during long silent stretches (e.g.
        while scoring a dense mesh). This module has no Slicer/Qt
        dependency itself; that's entirely the caller's business.

    Returns
    -------
    str
        Path to the generated pinna_heatmap.ply file.

    Raises
    ------
    NotImplementedError
        If Curvature Project v4 isn't fully set up yet -- see
        describe_configuration_problem() for specifics.
    TimeoutError
        If the subprocess doesn't finish within
        config.CURVATURE_SUBPROCESS_TIMEOUT_SEC (it's killed first).
    RuntimeError
        If the subprocess runs but exits with an error, or doesn't produce
        the expected output file -- its captured console output is
        included so the surgeon (or you, during testing) can see exactly
        what Curvature Project v4 reported.
    """
    problem = describe_configuration_problem()
    if problem:
        raise NotImplementedError(problem)

    output_dir = Path(output_dir)

    # Clear any leftover files from a previous run in this same scratch
    # folder. Without this, a run that crashes partway through (after an
    # earlier run had already succeeded once) would leave the *previous*
    # run's pinna_heatmap.ply sitting on disk -- and the "did the output
    # file get created" check below would then report success for a run
    # that actually failed, since it can't tell an old file from a new one.
    if output_dir.exists():
        shutil.rmtree(output_dir)
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True)

    # main.py expects these exact filenames, relative to wherever it's run
    # from -- see module docstring.
    shutil.copyfile(scutum_defect_mesh_path, data_dir / "scutum.stl")
    shutil.copyfile(pinna_mesh_path, data_dir / "pinna.stl")

    # "-u": forces fully unbuffered stdout/stderr in the child process.
    # Without it, Python block-buffers output when stdout isn't a real
    # terminal (which it isn't -- we're piping it), so main.py's progress
    # prints would arrive in occasional large bursts instead of promptly,
    # making the live progress feed feel stuck.
    command = [str(VENV_PYTHON_PATH), "-u", str(CURVATURE_PROJECT_MAIN_PATH)]
    logger.info("Running Curvature Project v4 in %s: %s", output_dir, " ".join(command))

    process = subprocess.Popen(
        command,
        cwd=str(output_dir),  # so main.py's Path("data")/Path("output") resolve here
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # merge, so an error appears in-order with the progress that preceded it
        text=True,
        bufsize=1,
    )

    line_queue: "queue.Queue" = queue.Queue()
    reader = threading.Thread(
        target=_stream_pipe_to_queue, args=(process.stdout, line_queue), daemon=True
    )
    reader.start()

    output_lines = []
    start_time = time.monotonic()
    stream_ended = False
    while not stream_ended:
        elapsed = time.monotonic() - start_time
        if elapsed > config.CURVATURE_SUBPROCESS_TIMEOUT_SEC:
            process.kill()
            raise TimeoutError(
                "Curvature Project v4 didn't finish within "
                f"{config.CURVATURE_SUBPROCESS_TIMEOUT_SEC}s and was stopped. "
                "Last output:\n" + "".join(output_lines[-40:])
            )
        try:
            line = line_queue.get(timeout=0.2)
        except queue.Empty:
            if progress_callback:
                progress_callback("")
            continue

        if line is None:
            stream_ended = True
            continue

        output_lines.append(line)
        if progress_callback:
            progress_callback(line.rstrip("\n"))

    returncode = process.wait()

    if returncode != 0:
        raise RuntimeError(
            f"Curvature Project v4 exited with an error (code {returncode}):\n"
            + "".join(output_lines[-60:])
        )

    heatmap_path = output_dir / "output" / "pinna_heatmap.ply"
    if not heatmap_path.is_file():
        raise RuntimeError(
            "Curvature Project v4 finished without error, but the expected "
            f"output file wasn't found at {heatmap_path}. Its printed output "
            "was:\n" + "".join(output_lines[-60:])
        )

    return str(heatmap_path)


def get_ranked_candidates_csv_path(output_dir: str) -> str:
    """
    Convenience helper: main.py also writes a ranked shortlist of harvest
    sites (vertex ids, coarse score, Chamfer/Hausdorff distance after ICP)
    to output/top_harvest_sites.csv, alongside the heatmap. Returns that
    path (same output_dir passed to run_curvature_comparison).
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
