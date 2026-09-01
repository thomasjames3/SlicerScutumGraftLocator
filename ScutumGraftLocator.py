"""
ScutumGraftLocator.py
==============================
The Slicer scripted module entry point. This file is what Slicer discovers
and loads as a module (its name must match the containing extension, per
Slicer's convention).

Structurally, this file is intentionally thin: it builds a QStackedWidget
from Resources/UI/*.ui files and the page controller classes in
ScutumGraftLocatorLib/pages/, in the order given by
ScutumGraftLocatorLib.wizard_state.PAGE_ORDER. It does not contain
any segmentation, meshing, or drawing logic itself -- all of that lives in
core/ and the individual page controllers, which were written and tested
independently of Slicer (see the project's earlier standalone testing).

If you want to reorder, add, or remove a wizard page, the one-line change
is in wizard_state.PAGE_ORDER -- this file will pick it up automatically
without needing to be edited.
"""

import os
import importlib

import qt
import slicer
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleWidget,
    ScriptedLoadableModuleLogic,
)

from ScutumGraftLocatorLib.wizard_state import WizardState, PAGE_ORDER, should_skip_page


def _remove_all_stacked_pages(stacked_widget):
    # Loop on widget(0) returning None rather than count() -- PythonQt (the
    # Slicer/Qt binding this module runs under) exposes QStackedWidget's
    # count as an already-evaluated int attribute, not a callable method,
    # so `stacked_widget.count()` raises "'int' object is not callable".
    while stacked_widget.widget(0) is not None:
        stacked_widget.removeWidget(stacked_widget.widget(0))


class ScutumGraftLocator(ScriptedLoadableModule):
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "Scutum Graft Locator"
        self.parent.categories = ["Surgical Planning"]
        self.parent.dependencies = []
        self.parent.contributors = ["Thomas James"]
        self.parent.helpText = (
            "A step-by-step wizard for segmenting the scutum defect and "
            "pinna from a CT scan, and generating a cartilage graft "
            "harvest-site heatmap via Curvature Project v4."
        )
        self.parent.acknowledgementText = ""


class ScutumGraftLocatorLogic(ScriptedLoadableModuleLogic):
    """Deliberately minimal -- almost all actual logic lives in core/ and
    the page controllers, which don't depend on Slicer's Logic base class
    at all. This class exists mainly to satisfy Slicer's expected module
    structure."""
    pass


class ScutumGraftLocatorWidget(ScriptedLoadableModuleWidget):
    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)

        ui_dir = os.path.join(os.path.dirname(__file__), "Resources", "UI")

        top_level_widget = slicer.util.loadUI(
            os.path.join(ui_dir, "ScutumGraftLocator.ui")
        )
        top_level_widget.setMRMLScene(slicer.mrmlScene)
        self.layout.addWidget(top_level_widget)
        self.ui = slicer.util.childWidgetVariables(top_level_widget)

        self.state = WizardState()
        self.state.working_dir = os.path.join(slicer.app.temporaryPath, "ScutumGraftLocator")
        os.makedirs(self.state.working_dir, exist_ok=True)

        self._page_meta = []
        self._controllers = {}
        self._current_index = 0

        self._build_pages(ui_dir)

        self.ui.backButton.clicked.connect(self._on_back_clicked)
        self.ui.nextButton.clicked.connect(self._on_next_clicked)

        self._show_page(0)

    def _build_pages(self, ui_dir):
        # NOTE: page controller modules (and therefore their imports of
        # core/, trimesh, SimpleITK, etc.) are intentionally NOT imported
        # here. Only the .ui files are loaded now -- that's just Qt widget
        # construction and doesn't touch any Python dependency. Each page's
        # actual controller module is imported lazily, the first time that
        # page is shown (see _get_or_create_controller below). This is what
        # lets the Setup page install missing packages *before* any later
        # page's imports are attempted -- importing everything upfront here
        # would defeat the whole point of the Setup page.
        self._page_meta = []  # (page_id, controller_class_name, page_widgets, page_ui_widget)
        self._controllers = {}  # page_id -> instantiated controller, filled in lazily

        for page_id, ui_filename, controller_class_name in PAGE_ORDER:
            page_ui_widget = slicer.util.loadUI(os.path.join(ui_dir, ui_filename))
            # Parent each page under stackedWidget by adding it (this is
            # the normal/safe way to parent a loadUI() widget), then
            # immediately pull it back out -- removeWidget() leaves the
            # widget parented to stackedWidget but no longer counted as one
            # of its pages (Qt docs: "Parent object and parent widget of
            # widget will remain the QStackedWidget"). _show_page() below
            # re-adds exactly one page at a time from here on. This also
            # takes care of discarding the .ui file's own placeholder page
            # (index 0), since it goes through the same strip pass.
            self.ui.stackedWidget.addWidget(page_ui_widget)
            page_widgets = slicer.util.childWidgetVariables(page_ui_widget)
            self._page_meta.append((page_id, controller_class_name, page_widgets, page_ui_widget))

        _remove_all_stacked_pages(self.ui.stackedWidget)

    def _get_or_create_controller(self, index):
        page_id, controller_class_name, page_widgets, _ = self._page_meta[index]

        if page_id not in self._controllers:
            page_module = importlib.import_module(
                f"ScutumGraftLocatorLib.pages.page_{page_id}"
            )
            controller_class = getattr(page_module, controller_class_name)
            self._controllers[page_id] = controller_class(page_widgets, self.state)

        return self._controllers[page_id]

    def _show_page(self, index, direction=1):
        # Some pages (currently just the tutorial-only welcome page) are
        # skipped entirely depending on wizard state -- checked via a plain
        # page_id -> state predicate (wizard_state.should_skip_page)
        # rather than instantiating the controller, so skip-checking never
        # forces an early import of a page module before Setup's install
        # step runs (see _get_or_create_controller's docstring). `direction`
        # says which way to keep looking if this page turns out to be
        # skipped, so Back/Next both skip over it correctly.
        page_id = self._page_meta[index][0]
        if should_skip_page(self.state, page_id):
            next_index = index + direction
            if 0 <= next_index < len(self._page_meta):
                self._show_page(next_index, direction)
                return
            # Can't skip further without running off the end of the wizard
            # -- show it anyway as a fallback (shouldn't happen in practice
            # since the first/last pages are never skipped).

        self._current_index = index
        controller = self._get_or_create_controller(index)

        # Only ever keep the page being shown inside stackedWidget (see
        # _build_pages). Qt's QStackedLayout computes sizeHint()/
        # minimumSize() as the max over every page it holds, even hidden
        # ones, so leaving every page in the stack permanently forced the
        # scroll area to reserve space for the tallest page anywhere in the
        # wizard -- producing a phantom scrollbar and blank space on every
        # shorter page, and (combined with the scrollbar's position not
        # resetting on navigation, handled below) making some page
        # transitions look like they'd jumped down the page.
        new_page_widget = self._page_meta[index][3]
        _remove_all_stacked_pages(self.ui.stackedWidget)
        self.ui.stackedWidget.addWidget(new_page_widget)
        self.ui.stackedWidget.setCurrentWidget(new_page_widget)

        visible_total = sum(
            1 for pid, _, _, _ in self._page_meta if not should_skip_page(self.state, pid)
        )
        visible_position = sum(
            1 for pid, _, _, _ in self._page_meta[: index + 1]
            if not should_skip_page(self.state, pid)
        )
        self.ui.pageIndicatorLabel.setText(f"Step {visible_position} of {visible_total}")
        self.ui.backButton.setEnabled(index > 0)
        self.ui.nextButton.setText("Finish" if controller.is_final_page() else "Next >")
        self.ui.wizardStatusLabel.setText("")

        controller.on_enter()

        # Always land at the top of the new page rather than wherever the
        # scrollbar happened to be left. Reset twice: once now, and once
        # after the event loop finishes the page's post-on_enter() layout
        # pass, in case that pass changes the scrollable range.
        self.ui.pageScrollArea.verticalScrollBar().setValue(0)
        qt.QTimer.singleShot(
            0, lambda: self.ui.pageScrollArea.verticalScrollBar().setValue(0)
        )

    def _on_back_clicked(self):
        if self._current_index == 0:
            return
        controller = self._get_or_create_controller(self._current_index)
        controller.on_leave_back()
        self._show_page(self._current_index - 1, direction=-1)

    def _on_next_clicked(self):
        controller = self._get_or_create_controller(self._current_index)
        ok, message = controller.on_leave_next()
        if not ok:
            self.ui.wizardStatusLabel.setText(message)
            return

        if self._current_index + 1 < len(self._page_meta):
            self._show_page(self._current_index + 1, direction=1)
        # else: this was the final page ("Finish") -- nothing further to do,
        # the Complete page's own Download button already handled saving
        # whichever result files the surgeon wanted.

    def cleanup(self):
        pass
