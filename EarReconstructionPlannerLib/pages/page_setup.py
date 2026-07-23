"""
page_setup.py
==============
Page 0: Setup. Checks whether the required Python packages are installed
in Slicer's environment and, if not, installs them with one button click.
This only does real work the first time the extension is ever used on a
given Slicer installation -- on every later launch, this page confirms
everything's ready and the surgeon can click Next immediately.

Expected widgets in page_setup.ui (see Resources/UI/page_setup.ui):
  - instructionLabel   (QLabel)
  - installButton      (QPushButton)
  - progressBar         (QProgressBar)
  - statusLabel         (QLabel)
"""

from __future__ import annotations
from EarReconstructionPlannerLib.pages.base_page import WizardPage
from EarReconstructionPlannerLib import dependencies


class SetupPage(WizardPage):
    def on_enter(self):
        self.ui.progressBar.setVisible(False)
        self._refresh_status()
        self.ui.installButton.clicked.connect(self._on_install_clicked)

    def _refresh_status(self):
        missing = dependencies.check_missing_packages()
        if not missing:
            self.ui.statusLabel.setText("All set -- everything needed is already installed.")
            self.ui.installButton.setEnabled(False)
        else:
            names = ", ".join(spec for _, spec in missing)
            self.ui.statusLabel.setText(
                f"The following need to be installed once: {names}"
            )
            self.ui.installButton.setEnabled(True)

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
            self.ui.statusLabel.setText("All set -- everything installed successfully.")

    def on_leave_next(self):
        missing = dependencies.check_missing_packages()
        if missing:
            return False, "Please finish installing the required packages before continuing."
        return True, ""
