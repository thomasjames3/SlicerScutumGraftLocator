"""
pinna_landmarks.py
==================
Landmark input needed to locate the pinna region, kept separate from
core/landmarks.py (which is ear-canal-specific) since the pinna needs a
much simpler input: just a single reference point, rather than the
2-point axis system the canal needs.

Why just one point instead of two: the ear canal's landmarks define an
axis because thresholding needs to know "which direction is
the canal" to pick the right connected component. The pinna's Stage A step
doesn't need an axis at all -- it just needs to know roughly where to
center a search region; the surgeon's drawn outline (added in a later
stage) does the actual shaping work.

Note: which ear (left/right) used to live here as a `side` field, picked
via radio buttons on this page. It's now `WizardState.ear_side`, chosen
once up front on the DICOM load page instead -- see that field's docstring
in wizard_state.py for why (a single source of truth instead of a
per-page field the rest of the pipeline had to trust was consistent).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple

Point3D = Tuple[float, float, float]  # (x, y, z) in RAS mm, Slicer's convention


@dataclass
class PinnaLandmarks:
    """
    ear_center:
        A single point clicked roughly in the middle of the pinna (e.g.
        near the concha, the bowl-shaped hollow at the ear's center).
        Doesn't need to be precise -- it just centers the search region.

        This is placed *before* any pinna segmentation exists (the pinna
        review page, which creates the first pinna-related 3D model,
        doesn't run until the step after this one) -- and before the
        scutum stage's own review/draw steps too (the wizard places the
        2-point scutum ear-canal axis early, then does the whole pinna
        stage, then comes back for scutum review/draw -- see
        "Pinna-first wizard reorder" in CLAUDE.md), so there is no model
        of any kind in the 3D view yet. Find this point on the slice views
        instead (same as the ear canal landmarks), not by looking for the
        pinna as a 3D model, since there isn't one yet.
    """

    ear_center: Optional[Point3D] = None

    def is_complete(self) -> bool:
        return self.ear_center is not None

    def validate(self) -> Optional[str]:
        """
        Returns a plain-English warning message if something looks off, or
        None if everything looks fine. Kept forgiving on purpose -- see
        EarCanalLandmarks.validate() in core/landmarks.py for the same
        philosophy.
        """
        if self.ear_center is None:
            return "Please click a point at the center of the pinna before continuing."
        return None


# Plain-language instruction shown in the wizard for this step. Kept as a
# module-level constant (matching the pattern in core/landmarks.py) so
# wording can be updated without touching UI layout code.
PINNA_LANDMARK_STEP = {
    "field": "ear_center",
    "instruction": "Using the slice views, click roughly the center of the pinna (for example, inside the bowl-shaped hollow).",
    "reference_image": "step_pinna_center.png",
}
