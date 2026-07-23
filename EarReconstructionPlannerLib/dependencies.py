"""
dependencies.py
=================
Checks for and installs the Python packages this extension needs, using
Slicer's own bundled Python environment (`slicer.util.pip_install`) rather
than assuming anything about the surgeon's system Python -- this is what
lets a surgeon with zero Python experience install everything with a
single button click on the wizard's first page.

This mirrors the same pattern used by other Slicer extensions that need
extra packages beyond what Slicer ships with (e.g. the MONAI Label and
nnU-Net plugins), and only needs to actually install anything once per
Slicer installation -- subsequent launches just confirm everything's
already present and skip straight past this page.
"""

from __future__ import annotations

# name -> the string passed to pip. Kept as a list of (import_name,
# pip_spec) pairs since some packages import under a different name than
# their pip package name (e.g. SimpleITK's import name matches, but this
# pattern is here in case that ever isn't true for a future dependency).
REQUIRED_PACKAGES = [
    ("SimpleITK", "SimpleITK>=2.3"),
    ("trimesh", "trimesh>=4.0"),
    ("networkx", "networkx>=3.0"),
    ("skimage", "scikit-image>=0.22"),
    ("scipy", "scipy>=1.11"),
]


def check_missing_packages() -> list:
    """
    Returns a list of (import_name, pip_spec) tuples for packages that
    aren't importable yet. An empty list means everything's ready.
    """
    missing = []
    for import_name, pip_spec in REQUIRED_PACKAGES:
        try:
            __import__(import_name)
        except ImportError:
            missing.append((import_name, pip_spec))
    return missing


def install_missing_packages(progress_callback=None) -> list:
    """
    Installs every currently-missing package via Slicer's pip_install.

    Parameters
    ----------
    progress_callback : callable, optional
        Called as progress_callback(index, total, package_name) before
        each install, so the Setup page can update a progress bar/label.
        Purely cosmetic -- installation still proceeds without one.

    Returns
    -------
    list
        Any packages that still failed to import after attempting install
        (empty list = complete success). Surfacing failures rather than
        raising lets the Setup page show a clear per-package error instead
        of a single opaque crash.
    """
    import slicer  # deferred import: only available when running inside Slicer

    still_missing = []
    missing = check_missing_packages()
    total = len(missing)

    for index, (import_name, pip_spec) in enumerate(missing):
        if progress_callback:
            progress_callback(index, total, pip_spec)
        try:
            slicer.util.pip_install(pip_spec)
            __import__(import_name)
        except Exception:
            still_missing.append((import_name, pip_spec))

    return still_missing
