# Ear Reconstruction Planner (Slicer Extension)

A 10-page wizard, running entirely inside 3D Slicer, that takes a surgeon
from a loaded CT scan through to a cartilage-graft-harvest-site heatmap:

```
1. Setup              -- one-time dependency install
2. Load DICOM         -- confirm which loaded scan to use
3. Scutum landmarks   -- place 2 points defining the ear canal axis
4. Pinna landmarks    -- place 1 point + pick left/right ear
5. Pinna review       -- run/adjust the skin-surface segmentation
6. Pinna draw         -- trace the pinna outline on the 3D mesh
7. Scutum review      -- run/adjust the bone-wall segmentation
8. Scutum draw        -- trace the defect outline on the 3D mesh
9. Verify             -- surgeon confirms both meshes look correct
10. Curvature          -- run Curvature Project v4, load the heatmap
```

The pinna stage runs before the scutum review/draw steps (only the 2-point
scutum ear-canal axis is placed early, since the pinna draw step needs its
direction) so a surgeon can redo the scutum defect shape repeatedly -- to
see how it changes the suggested harvest site -- without re-segmenting the
pinna each time. See "Pinna-first wizard reorder" in `CLAUDE.md`.

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

**Confirmed working end-to-end in real Slicer:** Setup through DICOM load
through both the scutum stage (landmarks -> bone-wall segmentation ->
drawn-outline defect isolation) and the pinna stage (landmarks ->
skin-surface segmentation -> drawn outline + seed point + canal-opening
marker -> isolated pinna patch, cropped toward the canal and cleaned of
disconnected islands). See `CLAUDE.md`'s "Current status" section and its
"Bugs already found and fixed" list for the full history of what broke
and how it was fixed along the way -- several were non-obvious Slicer API
or coordinate-convention gotchas worth reading before touching
`roi_crop.py`, `mesh_isolate.py`, or anything that pulls/pushes a
volume/mesh to Slicer.

**Not yet tested:** the Verify page (page 8) and the Curvature page (page
9) -- the wizard has never been walked all the way through to actually
running Curvature Project v4 and loading a heatmap. The subprocess
interface itself is no longer guesswork (`main.py` takes no CLI
arguments; it reads `data/scutum.stl`/`data/pinna.stl` and writes
`output/pinna_heatmap.ply`/`output/top_harvest_sites.csv`, relative to
its working directory -- see `curvature_integration.py`), but
`VENV_PYTHON_PATH`/`CURVATURE_PROJECT_MAIN_PATH` at the top of that file
may still be placeholders -- confirm they point at your actual Curvature
Project v4 install before expecting the final step to run.

**Known open issue:** the "Reset All Points" button on the scutum
landmarks page was reported broken early on and hasn't been debugged yet.

## Suggested next test

Since the scutum/pinna pipeline is now confirmed working, the natural
next step is walking all the way through Verify and Curvature for the
first time -- that's the last untested stretch of the wizard.
