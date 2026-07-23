# Ear Reconstruction Planner (Slicer Extension)

A 10-page wizard, running entirely inside 3D Slicer, that takes a surgeon
from a loaded CT scan through to a cartilage-graft-harvest-site heatmap:

```
1. Setup              -- one-time dependency install
2. Load DICOM         -- confirm which loaded scan to use
3. Scutum landmarks   -- place 4 points defining the ear canal
4. Scutum review      -- run/adjust the bone-wall segmentation
5. Scutum draw        -- trace the defect outline on the 3D mesh
6. Pinna landmarks    -- place 1 point + pick left/right ear
7. Pinna review       -- run/adjust the skin-surface segmentation
8. Pinna draw         -- trace the pinna outline on the 3D mesh
9. Verify             -- surgeon confirms both meshes look correct
10. Curvature          -- run Curvature Project v4, load the heatmap
```

## Installing in Slicer (for testing)

1. Open 3D Slicer.
2. **Edit > Application Settings > Modules**.
3. Under "Additional module paths", click **Add**, and select this
   `EarReconstructionPlanner` folder (the one containing
   `EarReconstructionPlanner.py`).
4. Restart Slicer when prompted.
5. The module will appear under the **Surgical Planning** category in the
   module dropdown, named "Ear Reconstruction Planner".

## Editing the interface in Qt Designer

Every page's visual layout lives in its own `.ui` file under
`Resources/UI/`, completely separate from the Python logic that drives it.
You can open and edit these directly:

- **From inside Slicer**: the Extension Wizard module (built into Slicer)
  has a "Edit UI file" style option, or you can launch Qt Designer directly
  from Slicer's install folder so it picks up Slicer's custom widgets
  (`qMRMLNodeComboBox`, `ctkSliderWidget`, etc.) correctly.
- **Standalone Qt Designer**: works too for basic edits (moving buttons,
  changing text, resizing), but won't recognize Slicer's custom widget
  types unless pointed at Slicer's plugin path -- for anything beyond
  plain Qt widgets, editing from within Slicer's own environment is more
  reliable.

Each `.ui` file's expected widget names (what the matching Python page
controller looks for) are documented in a comment at the top of that
page's `.py` file in `EarReconstructionPlannerLib/pages/` -- e.g.
`page_scutum_review.py` lists exactly which slider/button names
`page_scutum_review.ui` needs to keep working. You can freely rearrange,
restyle, or relabel things, just keep those object names intact (or update
both the `.ui` and its `.py` file together if you rename one).

## Current status -- what's real vs. what needs testing

**Carried over from standalone testing, already validated:** everything
under `EarReconstructionPlannerLib/core/` (segmentation, ROI cropping,
mesh export, region isolation) -- this is the exact code tested earlier
against synthetic scans outside of Slicer.

**Written against Slicer's documented API, not yet run in real Slicer:**
all of the wizard page controllers and the main module file. I can't run
actual Slicer in this environment to click through the wizard myself, so
treat the first real run as the actual test -- especially:
- `SetAndObserveSurfaceConstraintNode` in `page_scutum_draw.py` /
  `page_pinna_draw.py` (the API for snapping a drawn curve to a mesh
  surface -- flagged in that file's docstring as worth double-checking
  against your installed Slicer version).
- Widget property names on `ctkSliderWidget` (`.value`, `.minimum`,
  `.maximum`) -- correct for recent Slicer/CTK versions, but worth
  confirming if you're on an older Slicer release.

**Not yet connected:** the final Curvature Project v4 step
(`EarReconstructionPlannerLib/curvature_integration.py`). This runs it as
a separate subprocess in its own Python 3.12 venv (see that file's
docstring for why -- Slicer's embedded Python is very likely a different,
incompatible version for the compiled packages that project depends on).
It currently has placeholder command-line arguments since I don't have
that project's actual source yet. Once you're able to share it (a few key
files at a time works fine, or describing `main.py`'s actual CLI
arguments), I'll wire up the real command and output parsing.

## Suggested first test

Rather than clicking through all 10 pages at once the first time, load the
module, get through Setup and DICOM load, then place the 4 scutum
landmarks and try running the segmentation on page 4 -- that exercises the
most code (Slicer volume <-> SimpleITK bridging, ROI building, thresholded
segmentation, mesh export, model loading) in one step, and is the fastest
way to surface any real-Slicer API surprises early.
