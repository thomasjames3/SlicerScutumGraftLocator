# Scutum Graft Locator

A tool that runs inside **3D Slicer** (free, open-source medical imaging
software) to preoperatively assist with scutum reconstruction in various operations. 
Starting from a patient's CT scan, it walks you step-by-step through marking the ear
canal and the pinna (the ear used as the cartilage donor site), then compares the
shape and curvature of the scutum defect against the pinna's surface and highlights the
best place to harvest the cartilage graft.

No programming knowledge is needed — everything is clicking buttons and
placing points on the 3D image, and the one-time setup step installs
everything else automatically.

---

## What you'll need

- **3D Slicer** installed on your computer (free, all platforms) —
  download it from [slicer.org](https://www.slicer.org/) if you don't
  already have it.
- A patient CT scan in DICOM format, loaded into Slicer (or ready to load).
- An internet connection the *first* time you run the tool, so it can
  install a few extra components it needs (one-time only).

---

## Step 1: Download this tool

1. On this page, click the green **`< > Code`** button near the top.
2. Click **Download ZIP**.
3. Find the downloaded file (usually in your Downloads folder) and
   **extract/unzip it** to a permanent location on your computer — for
   example, `Documents\ScutumGraftLocator`. Right-click the ZIP and choose
   "Extract All..." on Windows, or double-click it on Mac.
4. After extracting, you should have a folder named `ScutumGraftLocator`
   that directly contains a file called `ScutumGraftLocator.py`. Remember
   where this folder is — you'll point Slicer at it in the next step.

*(If you're comfortable with `git`, cloning the repository works the same
way — either method just needs to end with a `ScutumGraftLocator` folder
sitting somewhere permanent on your computer.)*

---

## Step 2: Install it into 3D Slicer

You only need to do this once. It tells Slicer where to find this tool.

1. Open **3D Slicer**.
2. From the top menu, go to **Edit → Application Settings → Modules**.
3. Under **"Additional module paths"**, click **Add**.
4. Select the `ScutumGraftLocator` folder from Step 1 (the one containing
   `ScutumGraftLocator.py`).
5. Click **OK**, then **restart Slicer** when it prompts you to.
6. After restarting, open the module dropdown (top-left of the Slicer
   window) and look under the **Surgical Planning** category for
   **"Scutum Graft Locator"**. Select it to open the wizard.

---

## Step 3: One-time setup inside the tool

The first page of the wizard is **Setup**. Click **Install**, and it will
automatically download the few extra Python components the tool needs
(this only requires an internet connection the first time — every launch
after that skips straight past this page). Also on this page, choose:

- **Normal mode** — just the wizard, no extra explanations.
- **Tutorial mode** — adds a short orientation page and extra
  step-by-step guidance (things like how to place a point, rotate the 3D
  view, or draw on a surface) on every page. Recommended the first time
  you use the tool.

---

## Step 4: Walking through the wizard

Load your patient's CT scan into Slicer first (**File → Add DICOM Data**),
then open the module and go through each page in order. You can always go
back to an earlier page to redo something — later results tied to that
page are cleared and recomputed automatically.

| Page | What you do |
|---|---|
| **1. Setup** | One-time install + choose Normal/Tutorial mode (Step 3 above). |
| **2. Welcome** | (Tutorial mode only) A short orientation to the wizard. |
| **3. Load DICOM** | Confirm which loaded CT scan the tool should use. |
| **4. Scutum landmarks** | Click 2 points on the CT to mark the ear canal's direction (the bony canal opening, then a point near the eardrum). |
| **5. Pinna landmarks** | Click 1 point roughly at the center of the pinna (the ear that will donate cartilage), and confirm which side (left/right). |
| **6. Pinna review** | Click a button to automatically outline the pinna's skin surface from the CT, then fine-tune it live if needed (adjust the threshold, paint, or erase) until it looks right in 3D. |
| **7. Pinna draw** | Trace the outline of the region of the pinna you want to consider, directly on the 3D surface, then click a seed point inside it to isolate that patch. |
| **8. Scutum review** | Same idea as page 6, but for the bone wall around the ear canal defect — auto-segment, then fine-tune live until the 3D shape matches what you expect. |
| **9. Scutum draw** | Trace the outline of the defect region on the 3D bone-wall surface, the same way as page 7. |
| **10. Verify** | Look over both isolated 3D shapes (pinna patch and scutum defect) and check the two approval boxes once you're satisfied they're correct. This is a deliberate manual checkpoint before the final comparison runs. |
| **11. Curvature** | Click Run. The tool compares the shape of the scutum defect against the pinna patch and produces a color heatmap on the pinna showing the best harvest site(s), plus a ranked list of candidate sites. Use the "Locate" buttons in the results table to show/hide markers for each candidate directly in the 3D view. |
| **12. Complete** | Download the files you want to keep: the heatmap, the scutum defect mesh, the isolated pinna mesh, and the scutum bone-wall mesh. Each option is only available if that file was actually generated. |

A couple of things worth knowing as you go:

- **Why pinna comes before the scutum defect steps:** if you redraw or
  re-outline the scutum defect shape to see how it changes the suggested
  harvest site, you don't have to redo the pinna segmentation each time —
  only the scutum-side steps need to be repeated.
- **Sliders, not typed numbers**, and **drawing on the 3D surface, not
  typed coordinates** — every adjustable setting in the wizard is meant to
  be usable without knowing what the underlying numbers mean.
- If something looks wrong on a review page (page 6 or 8), you can adjust
  the segmentation live — threshold slider, paint, erase, or smoothing —
  before moving on, the same way you would in Slicer's own Segment Editor.

---

## Troubleshooting

- **The module doesn't appear after restarting Slicer**: double check the
  "Additional module paths" entry in Step 2 points directly at the folder
  containing `ScutumGraftLocator.py` (not a parent or child folder).
- **Setup's Install button fails**: check your internet connection — the
  first run needs to download a few packages. If it keeps failing, restart
  Slicer and try again from the Setup page.
- **The 3D view looks empty after a step that reported success**: try
  scrolling/zooming out in the 3D view, or going back one page and
  forward again. If it persists, note which page it happened on before
  reporting the issue.

---

## For developers

Everything below this point is for anyone modifying the code itself, not
needed to just use the tool.

### Editing the interface in Qt Designer

Every page's visual layout lives in its own `.ui` file under
`Resources/UI/`, completely separate from the Python logic that drives it.
You can open and edit these directly:

- **From inside Slicer**: the Extension Wizard module (built into Slicer)
  has an "Edit UI file" style option, or you can launch Qt Designer
  directly from Slicer's install folder so it picks up Slicer's custom
  widgets (`qMRMLNodeComboBox`, `ctkSliderWidget`, etc.) correctly.
- **Standalone Qt Designer**: works too for basic edits (moving buttons,
  changing text, resizing), but won't recognize Slicer's custom widget
  types unless pointed at Slicer's plugin path — for anything beyond plain
  Qt widgets, editing from within Slicer's own environment is more
  reliable.

Each `.ui` file's expected widget names (what the matching Python page
controller looks for) are documented in a comment at the top of that
page's `.py` file in `ScutumGraftLocatorLib/pages/` — e.g.
`page_scutum_review.py` lists exactly which slider/button names
`page_scutum_review.ui` needs to keep working. You can freely rearrange,
restyle, or relabel things, just keep those object names intact (or update
both the `.ui` and its `.py` file together if you rename one).

### Architecture

`ScutumGraftLocatorLib/core/` is completely Slicer-independent and
unit-testable with synthetic NumPy/SimpleITK/trimesh data.
`ScutumGraftLocatorLib/pages/` holds one controller per wizard page, each
paired with a `.ui` file. The curvature comparison
(`core/curvature/`) is a from-scratch, in-process port of the standalone
"Curvature Project v4" reference tool (no subprocess, no separate Python
environment) — see `CLAUDE.md` for the full history and current status of
every part of the project, including what's confirmed working on real
scans versus what's still provisional.
