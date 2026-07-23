"""
base_page.py
=============
Common interface every wizard page controller implements. The main module
widget (EarReconstructionPlanner.py) treats every page identically through
this interface -- it doesn't need a special case for "the page with the
threshold slider" vs. "the page with the curve-drawing tool"; it just calls
these same four methods on whichever page is currently showing.

Each page controller receives:
  - `ui`: the loaded .ui widget for that page (a plain Qt widget -- widgets
    inside it are accessed as attributes, e.g. `ui.nextButton`, standard
    Slicer/PythonQt convention for loaded .ui files)
  - `state`: the single shared WizardState instance (see wizard_state.py)

Subclasses override whichever of the four lifecycle methods they need;
sensible no-op defaults are provided here so a simple page (e.g. one with
no validation) doesn't need to override anything but on_enter/on_leave_next.
"""

from __future__ import annotations


class WizardPage:
    def __init__(self, ui, state):
        self.ui = ui
        self.state = state

    def on_enter(self):
        """
        Called every time this page becomes visible (including when
        navigating back to it). Use this to populate/refresh widgets from
        `self.state`, e.g. re-showing previously placed landmarks.
        """
        pass

    def on_leave_next(self):
        """
        Called when the surgeon clicks "Next". Do any work this page is
        responsible for (running a segmentation, saving a mesh, etc.) and
        validate that it's safe to proceed.

        Returns
        -------
        (bool, str)
            (True, "") to proceed to the next page, or
            (False, "some message") to stay on this page and show the
            message to the surgeon (e.g. "Please place all 4 points
            before continuing.").
        """
        return True, ""

    def on_leave_back(self):
        """
        Called when the surgeon clicks "Back". Most pages don't need to do
        anything here (no validation needed to go backward) -- override
        only if leaving this page backward needs cleanup.
        """
        pass

    def is_final_page(self) -> bool:
        """Override to return True on the last page, where "Next" should
        instead read "Finish" or similar in the main widget."""
        return False
