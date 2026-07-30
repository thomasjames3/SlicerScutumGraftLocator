"""
page_setup.py
==============
Page 0: Setup. Checks whether the required Python packages are installed
in Slicer's environment and, if not, installs them with one button click.
This only does real work the first time the extension is ever used on a
given Slicer installation -- on every later launch, this page confirms
everything's ready and the surgeon can click Next immediately.

Expected widgets in page_setup.ui (see Resources/UI/page_setup.ui):
  - normalModeRadioButton    (QRadioButton) -- default checked
  - tutorialModeRadioButton  (QRadioButton)
  - instructionLabel   (QLabel)
  - installButton      (QPushButton)
  - progressBar         (QProgressBar)
  - statusLabel         (QLabel)
  - restartSlicerButton (QPushButton) -- see _on_restart_clicked()'s docstring
    for why this exists: some freshly pip-installed packages (particularly
    ones with compiled/binary components, e.g. fast-simplification) don't
    reliably finish loading into an already-running Slicer process,
    especially on Windows where an already-loaded DLL can't always be
    safely replaced. Only shown (see _refresh_status()) when there's
    something to install/restart for, i.e. hidden on a return visit where
    everything was already installed in a prior, properly-restarted
    session -- otherwise a returning surgeon would see an unexplained
    restart option with no missing packages to justify it. Safe to offer
    at all only because this is page 0, before any DICOM/landmarks/
    segmentation state exists yet -- restarting here can never discard
    in-progress wizard work.

Mode choice (state.tutorial_mode) is made here, once, before anything else
in the wizard -- every later page reads that single flag (via
base_page.WizardPage.set_tutorial_text()) rather than asking again.
"""

from __future__ import annotations
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from EarReconstructionPlannerLib import dependencies


class SetupPage(WizardPage):
    def on_enter(self):
        self.ui.progressBar.setVisible(False)
        self._refresh_status()
        self.ui.installButton.clicked.connect(self._on_install_clicked)
        self.ui.restartSlicerButton.clicked.connect(self._on_restart_clicked)

        # Restore from state so re-entering this page (there's no earlier
        # page to come back from, but on_enter can still re-run) doesn't
        # lose a previously made choice.
        self.ui.tutorialModeRadioButton.setChecked(self.state.tutorial_mode)
        self.ui.normalModeRadioButton.setChecked(not self.state.tutorial_mode)
        self.ui.normalModeRadioButton.toggled.connect(self._on_mode_toggled)
        self.ui.tutorialModeRadioButton.toggled.connect(self._on_mode_toggled)

    def _on_mode_toggled(self, _checked):
        self.state.tutorial_mode = self.ui.tutorialModeRadioButton.isChecked()

    def _refresh_status(self):
        missing = dependencies.check_missing_packages()
        if not missing:
            self.ui.statusLabel.setText("All set -- everything needed is already installed.")
            self.ui.installButton.setEnabled(False)
            # No restart needed on a return visit where everything was
            # already installed in a prior (properly restarted) session --
            # only show this button when there's actually something to
            # install/restart for, so returning surgeons on an
            # already-set-up Slicer aren't confused by an unexplained
            # restart option.
            self.ui.restartSlicerButton.setVisible(False)
        else:
            names = ", ".join(spec for _, spec in missing)
            self.ui.statusLabel.setText(
                f"The following need to be installed once: {names}"
            )
            self.ui.installButton.setEnabled(True)
            self.ui.restartSlicerButton.setVisible(True)

    def _on_install_clicked(self):
        missing = dependencies.check_missing_packages()
        total = len(missing)
        self.ui.progressBar.setVisible(True)
        self.ui.progressBar.setMaximum(max(total, 1))
        self.ui.installButton.setEnabled(False)

        def progress_callback(index, total, package_name):
            self.ui.progressBar.setValue(index)
            self.ui.statusLabel.setText(f"Installing {package_name}...")
            # Let the UI repaint during what can be a slow pip install.
            import slicer
            slicer.app.processEvents()

        still_missing = dependencies.install_missing_packages(progress_callback)
        self.ui.progressBar.setValue(total)

        if still_missing:
            names = ", ".join(spec for _, spec in still_missing)
            self.ui.statusLabel.setText(
                f"Could not install: {names}. Check your internet connection "
                "and try again, or contact whoever set up this tool for help."
            )
            self.ui.installButton.setEnabled(True)
        else:
            self.ui.statusLabel.setText(
                "All set -- everything installed successfully. Please restart "
                "3D Slicer now (click 'Restart Slicer' below) before continuing."
            )

    def _on_restart_clicked(self):
        """Restarts the whole Slicer application via slicer.util.restart().

        Only offered on this page (Setup, page 0) -- always safe here since
        nothing has been loaded/placed/segmented yet at this point in the
        wizard, unlike a restart offered from any later page, which would
        discard real in-progress surgeon work. Still confirms first since
        restarting closes the entire application, not just this module.
        """
        import slicer

        if slicer.util.confirmYesNoDisplay(
            "This will restart 3D Slicer. Continue?"
        ):
            slicer.util.restart()

    def on_leave_next(self):
        missing = dependencies.check_missing_packages()
        if missing:
            return False, "Please finish installing the required packages before continuing."
        return True, ""
