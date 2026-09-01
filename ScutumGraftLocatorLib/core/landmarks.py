"""
landmarks.py
============
Defines the small set of anatomical points a surgeon clicks on the scan to
"tell" the pipeline roughly where the ear canal is. This replaces having a
fully automatic AI localizer (which would need a lot more training data than
we currently have) with a few seconds of clicking that any surgeon can do
correctly on the first try, because each point has a plain description and
a reference picture in the wizard UI.

Originally inspired by the 4-control-point VOI setup in Matin-Mann et al.
(2025) (2 axis points + 2 extra points to fix the bounding planes'
orientation), but simplified to just the 2 axis points -- see
roi_crop.build_roi_mask() for why: the 2 extra "reference" points required
the surgeon's click to land precisely on one side of a plane, which
directly contradicted their own "doesn't need to be precise" instruction,
and repeatedly produced bad or empty ROIs from perfectly reasonable,
slightly-imprecise clicks. The ROI only ever needs to *comfortably contain*
the canal (the real boundary comes from thresholding within it), so
perpendicular bounding planes derived automatically from these 2 points
work just as well with none of that fragility.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple

Point3D = Tuple[float, float, float]  # (x, y, z) in RAS mm, Slicer's convention


@dataclass
class EarCanalLandmarks:
    """
    The 2 points that define the ear canal axis and the initial region of
    interest.

    canal_opening:
        The opening of the bony ear canal -- "where the canal starts."

    near_eardrum:
        Just lateral to the eardrum, inside the canal -- "where the canal
        ends, right before the eardrum."

    Both fields start as None and get filled in one at a time as the
    surgeon clicks through the wizard. is_complete() tells the UI when it's
    safe to move to the next step.
    """

    canal_opening: Optional[Point3D] = None
    near_eardrum: Optional[Point3D] = None

    def is_complete(self) -> bool:
        return self.canal_opening is not None and self.near_eardrum is not None

    def validate(self) -> Optional[str]:
        """
        Sanity-checks the placed points and returns a plain-English warning
        message if something looks off, or None if everything looks fine.

        This is intentionally forgiving -- it's a safety net that catches
        obvious mistakes (e.g. clicking the same spot twice), not a strict
        geometry validator. It is shown to the surgeon before segmentation
        runs, with an option to go back and re-click a point.
        """
        if not self.is_complete():
            return "Please place both points before continuing."

        import math

        canal_length = math.dist(self.canal_opening, self.near_eardrum)
        # Canal opening and eardrum point should be a plausible distance
        # apart for an adult ear canal (roughly 10-25 mm per the literature
        # ranges cited in the EEC paper).
        if canal_length < 5.0:
            return (
                "The 'canal opening' and 'near eardrum' points are very "
                "close together (less than 5 mm apart). Please double-check "
                "these two points."
            )
        if canal_length > 40.0:
            return (
                "The 'canal opening' and 'near eardrum' points are further "
                "apart than expected (over 40 mm). Please double-check "
                "these two points."
            )

        return None

    @classmethod
    def from_slicer_fcsv(cls, path: str) -> "EarCanalLandmarks":
        """
        Load landmarks from a 3D Slicer Markups fiducial file (.fcsv).

        This is the practical way to get real landmark points into the
        pipeline before the Slicer wizard UI (slicer_module/) exists:

          1. In 3D Slicer, create a Markups > Fiducial node.
          2. Place exactly 2 points on the scan.
          3. Rename each point's LABEL (double-click it in the Markups
             module's control point list) to exactly one of:
                 canal_opening
                 near_eardrum
          4. Right-click the fiducial node in Data > Export/Save, or use
             File > Save Data, and save as a .fcsv file.
          5. Pass that .fcsv path to main.py --landmarks.

        Slicer stores fiducial coordinates in LPS (Left-Posterior-Superior)
        by default in the .fcsv header, but by far the most common Slicer
        export/display convention -- and the one this whole pipeline
        assumes -- is RAS (Right-Anterior-Superior). This function checks
        the file's own "CoordinateSystem" header line and converts to RAS
        automatically if needed, so you don't have to think about it.
        """
        import csv

        points_by_label = {}
        coordinate_system = "RAS"  # Slicer's default assumption if unspecified

        with open(path, newline="") as f:
            for line in f:
                if line.startswith("# CoordinateSystem"):
                    coordinate_system = line.split("=")[-1].strip()
                if not line.startswith("#"):
                    break

        with open(path, newline="") as f:
            data_lines = [l for l in f if not l.startswith("#") and l.strip()]

        for row in csv.reader(data_lines):
            if len(row) < 12:
                continue
            # Standard Slicer .fcsv columns:
            # id,x,y,z,ow,ox,oy,oz,vis,sel,lock,label,desc,associatedNodeID
            x, y, z = float(row[1]), float(row[2]), float(row[3])
            label = row[11].strip()
            if coordinate_system.upper().startswith("LPS"):
                x, y = -x, -y  # LPS -> RAS conversion
            points_by_label[label] = (x, y, z)

        missing = [
            field
            for field in ("canal_opening", "near_eardrum")
            if field not in points_by_label
        ]
        if missing:
            raise ValueError(
                f"The landmark file '{path}' is missing point(s) labeled: "
                f"{', '.join(missing)}. Found labels: "
                f"{list(points_by_label.keys())}. Double-check each point's "
                "LABEL in Slicer's Markups module matches these names exactly."
            )

        return cls(
            canal_opening=points_by_label["canal_opening"],
            near_eardrum=points_by_label["near_eardrum"],
        )

    @classmethod
    def from_json(cls, path: str) -> "EarCanalLandmarks":
        """
        Load landmarks from a simple custom JSON file, e.g.:

            {
              "canal_opening": [12.3, -45.6, 78.9],
              "near_eardrum": [11.0, -40.2, 65.4]
            }

        This is a simpler alternative to from_slicer_fcsv() if you'd rather
        hand-type or script-generate landmark coordinates instead of using
        Slicer's Markups module directly.
        """
        import json

        with open(path) as f:
            data = json.load(f)

        missing = [
            field
            for field in ("canal_opening", "near_eardrum")
            if field not in data
        ]
        if missing:
            raise ValueError(
                f"The landmark file '{path}' is missing key(s): {', '.join(missing)}"
            )

        return cls(
            canal_opening=tuple(data["canal_opening"]),
            near_eardrum=tuple(data["near_eardrum"]),
        )


# Plain-language instructions + reference image filenames shown in the
# wizard, one per step. Keeping this list here (rather than hardcoded in the
# Slicer UI file) means updating the wording doesn't require touching any
# UI layout code.
LANDMARK_STEPS = [
    {
        "field": "canal_opening",
        "instruction": "Click at the opening of the bony ear canal.",
        "reference_image": "step1_canal_opening.png",
    },
    {
        "field": "near_eardrum",
        "instruction": "Click just outside the eardrum, at the inner end of the canal.",
        "reference_image": "step2_near_eardrum.png",
    },
]
