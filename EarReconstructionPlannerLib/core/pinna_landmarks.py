"""
pinna_landmarks.py
==================
Landmark input needed to locate the pinna region, kept separate from
core/landmarks.py (which is ear-canal-specific) since the pinna needs a
much simpler input: a single reference point plus which ear, rather than
the 2-point axis system the canal needs.

Why just one point instead of two: the ear canal's landmarks define an
axis because thresholding needs to know "which direction is
the canal" to pick the right connected component. The pinna's Stage A step
doesn't need an axis at all -- it just needs to know roughly where to
center a search region; the surgeon's drawn outline (added in a later
stage) does the actual shaping work.
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

    side:
        Either "left" or "right", telling the pipeline which ear this is.
        Surgeons pick this from a simple dropdown/toggle in the wizard
        rather than it being inferred, since guessing wrong silently would
        be a bad failure mode for a surgical planning tool.
    """

    ear_center: Optional[Point3D] = None
    side: Optional[str] = None

    def is_complete(self) -> bool:
        return self.ear_center is not None and self.side in ("left", "right")

    def validate(self) -> Optional[str]:
        """
        Returns a plain-English warning message if something looks off, or
        None if everything looks fine. Kept forgiving on purpose -- see
        EarCanalLandmarks.validate() in core/landmarks.py for the same
        philosophy.
        """
        if self.ear_center is None:
            return "Please click a point at the center of the ear before continuing."
        if self.side not in ("left", "right"):
            return "Please specify whether this is the left or right ear."
        return None


# Plain-language instruction shown in the wizard for this step. Kept as a
# module-level constant (matching the pattern in core/landmarks.py) so
# wording can be updated without touching UI layout code.
PINNA_LANDMARK_STEP = {
    "field": "ear_center",
    "instruction": "Click roughly the center of the ear (for example, inside the bowl-shaped hollow).",
    "reference_image": "step_pinna_center.png",
}
