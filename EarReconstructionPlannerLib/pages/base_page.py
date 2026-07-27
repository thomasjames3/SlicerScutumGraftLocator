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
            message to the surgeon (e.g. "Please place both points
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

    def set_tutorial_text(self, text: str) -> None:
        """Show extra step-by-step guidance in a page's `tutorialLabel`
        widget, visible only when the surgeon chose Tutorial Mode on the
        Setup page (self.state.tutorial_mode). Every page's .ui includes a
        `tutorialLabel` QLabel (hidden by default) for this -- pages call
        this instead of touching that widget's visibility/text directly, so
        the "only show in tutorial mode" rule lives in one place. Safe to
        call even if a page's .ui doesn't have the widget (defensive, since
        every current page does)."""
        label = getattr(self.ui, "tutorialLabel", None)
        if label is None:
            return
        label.setText(text)
        label.setVisible(self.state.tutorial_mode and bool(text))

    def recenter_3d_view(self) -> None:
        """Recenter the 3D view on whatever just got loaded -- equivalent to
        clicking the "center 3D view" button -- and orient the camera to
        look from the correct anatomical side for this patient's ear
        (self.state.ear_side, chosen once on the DICOM load page and shared
        by every later page, rather than re-asked per page -- see
        wizard_state.py). Looking from the wrong side would show a left
        ear's model mirrored from how the surgeon actually sees it, so
        `ear_side` picks Left vs. Right rather than always defaulting to
        Right. Shared by every page that loads a new model into the 3D
        view (scutum/pinna review, curvature) so they all orient
        consistently instead of each hand-rolling the same three calls.

        NOTE: lookFromAxis()/ctkAxesWidget mirror the same API the 3D
        view's own axes-widget buttons use -- confirmed via Slicer/CTK
        source, but NOT YET CONFIRMED against a real Slicer install.
        """
        import slicer
        import ctk

        three_d_view = slicer.app.layoutManager().threeDWidget(0).threeDView()
        three_d_view.resetFocalPoint()
        three_d_view.resetCamera()

        axis = {
            "left": ctk.ctkAxesWidget.Left,
            "right": ctk.ctkAxesWidget.Right,
        }.get(self.state.ear_side)
        if axis is not None:
            three_d_view.lookFromAxis(axis)

    def set_absolute_point_size(self, markups_node, size_mm: float) -> None:
        """Give a Markups node (curve or fiducial) a fixed physical point
        size (in mm) instead of Slicer's default screen-relative
        percentage sizing. Works around a real bug seen in practice on the
        draw pages: right after a curve/fiducial node is freshly created,
        the 3D view's camera-derived screen-scale factor can be stale
        (left over from whatever the previous page's camera state was),
        making points render far too large until the view is recentered.
        A fixed mm size has no dependency on that computation at all.
        Deliberately does NOT touch the camera (unlike recenter_3d_view())
        -- the draw pages tell the surgeon to set up their camera angle
        before drawing, and this must not disturb that.

        `size_mm` is a required argument, not read from config here,
        because the scutum and pinna draw pages need different values
        (config.SCUTUM_DRAW_POINT_SIZE_MM / config.PINNA_DRAW_POINT_SIZE_MM
        -- the two outlines are drawn at very different physical scales).
        Call this right after creating any markups node on a draw page."""
        display_node = markups_node.GetDisplayNode()
        if display_node is None:
            markups_node.CreateDefaultDisplayNodes()
            display_node = markups_node.GetDisplayNode()
        if display_node is not None:
            display_node.SetUseGlyphScale(False)
            display_node.SetGlyphSize(size_mm)
