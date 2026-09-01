"""
page_welcome.py
==================
Tutorial-mode-only orientation page, shown right after Setup, explaining
how the wizard's shared UI works (Back/Next, the "can't skip ahead" rule,
and each page's own Reset/Revert options) before any real work begins.

Skipped entirely in Normal mode -- see wizard_state.SKIP_PAGE_IF and
EarReconstructionPlanner.py's _show_page() for how skipping works. This
page owns no WizardState fields (there's nothing on it to reset/revert).

Expected widgets in page_welcome.ui:
  - titleLabel  (QLabel)
  - bodyLabel   (QLabel)
"""

from __future__ import annotations
from EarReconstructionPlannerLib.pages.base_page import WizardPage


class WelcomePage(WizardPage):
    def on_enter(self):
        self.ui.bodyLabel.setText(
            "Since you picked Tutorial mode, here's a quick orientation "
            "before you start:\n\n"
            "- Back / Next: move between steps using the two buttons at "
            "the bottom of this window. You can always go Back to an "
            "earlier step to double-check or redo something.\n\n"
            "- You can't skip ahead: clicking Next checks that the current "
            "step is actually finished (e.g. all points placed, a "
            "segmentation has been run). If something's missing, you'll "
            "stay on this page and see a message explaining what's still "
            "needed.\n\n"
            "- 'Undo Options', near the bottom of most pages:\n"
            "  - 'Reset' / 'Reset This Page' clears only what you've done "
            "on the current page, so you can redo it from scratch.\n"
            "  - 'Revert to Here' keeps the current page's result but "
            "clears every step after it -- use this if you want to go "
            "back and redo something later in the wizard without losing "
            "what led up to it.\n\n"
            "- Every page in Tutorial mode has extra italic instructions "
            "below the main controls, explaining exactly what to do and "
            "how to use Slicer's tools (rotating the 3D view, drawing "
            "outlines, etc.).\n\n"
            "Click Next when you're ready to begin."
        )
