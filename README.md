# Scutum Graft Locator

A plug-in for 3D Slicer that preoperatively assists scutum reconstruction by locating the 
best auricular graft harvest sites. Starting from a patient's CT scan, it walks the user
through segmentation, isolating the proposed scutum defect and graft harvest region, then 
compares the shape and curvature of the scutum defect against the pinna region to highlight
the optimal sites to harvest the cartilage graft.

No background knowledge of segmentation or programming is required - the whole process is
semi-automated and gives step-by-step instructions to the user, including installation steps
on this page.

---

## What you'll need

- **3D Slicer** installed on your computer (free, all platforms) —
  download it from [slicer.org](https://www.slicer.org/).
- A patient CT scan in DICOM format.
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
   where this folder is — you'll need it in Slicer in the next step.

*(If you're comfortable with `git`, cloning the repository works the same
way — either method just needs to end with a `ScutumGraftLocator` folder
sitting somewhere permanent on your computer.)*

---

## Step 2: Install the plug-in into 3D Slicer

You only need to do this once. It tells Slicer where to find this tool.

1. Open **3D Slicer**.
2. From the top menu, go to **Edit → Application Settings → Modules**.
3. Under **"Additional module paths"**, click **Add**.
4. Select the `ScutumGraftLocator` folder from Step 1.
5. Click **OK**, then **restart Slicer** when it prompts you to.
6. After restarting, open the module dropdown (top-left of the Slicer
   window) and look under the **Surgical Planning** category for
   **"Scutum Graft Locator"**. Select it to open the wizard.

---

## Step 3: One-time setup inside the tool

The first page of the wizard is **Setup**. Click **Install**, and it will
automatically download the few extra Python components the tool needs.
You will have to restart Slicer to continue from here. This is a one-time 
installation, so this step will be skipped in future use. 
Also on this page, choose from:

- **Normal mode** — just the wizard, no extra explanations.
- **Tutorial mode** — adds a short orientation page and extra
  step-by-step guidance on every page, including basic navigation of Slicer.
  Recommended the first time you use the tool.

---

## Step 4: Walking through the wizard

Load your patient's CT scan into Slicer first (**File → Add DICOM Data**),
then open the module and follow each page in order. There are options to go
back to previous pages and reset steps in case of errors.

| Page | What you do |
|---|---|
| **1. Setup** | One-time install + choose Normal/Tutorial mode (Step 3 above). |
| **2. Welcome** | (Tutorial mode only) A short orientation to the wizard. |
| **3. Load DICOM** | Confirm which loaded CT scan the tool should use. |
| **4. Scutum landmarks** | Click 2 points on the CT to mark the ear canal's direction (the bony canal opening, then a point near the eardrum). |
| **5. Pinna landmarks** | Click 1 point roughly at the center of the pinna (the ear that will donate cartilage), and confirm its side (left/right). |
| **6. Pinna review** | Click a button to automatically segment the pinna's skin surface from the CT, then fine-tune it until it looks right in 3D. |
| **7. Pinna draw** | Trace the outline of the region of the pinna you want to consider, directly on the 3D surface, then click a seed point inside it to isolate that patch. |
| **8. Scutum review** | Same idea as page 6, but for the bone wall around the ear canal defect — auto-segment, then fine-tune until the 3D shape matches what you expect. |
| **9. Scutum draw** | Trace the outline of the defect region on the 3D bone-wall surface, the same way as page 7. |
| **10. Verify** | Look over both isolated 3D shapes (pinna region and scutum defect) and check the two approval boxes once you're satisfied they're correct. |
| **11. Curvature** | Click Run. The tool compares the shape of the scutum defect against the pinna patch and produces a color heatmap on the pinna showing the best harvest site(s), plus a ranked list of candidate sites. Use the "Locate" buttons in the results table to show/hide markers for each candidate directly in the 3D view. |
| **12. Complete** | Download the files you want to keep: the heatmap, the scutum defect mesh, the isolated pinna mesh, and the scutum bone-wall mesh. |


--- 

### Architecture

`ScutumGraftLocatorLib/core/` is completely Slicer-independent and
unit-testable with synthetic NumPy/SimpleITK/trimesh data.
`ScutumGraftLocatorLib/pages/` holds one controller per wizard page, each
paired with a `.ui` file. The curvature comparison
(`core/curvature/`) is a from-scratch, in-process port of the previous standalone
Curvature Comparison reference tool (no subprocess, no separate Python
environment).
