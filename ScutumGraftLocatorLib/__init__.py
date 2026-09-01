"""
ScutumGraftLocatorLib/__init__.py
=========================================
This package bundles everything the extension needs: the segmentation
`core/` modules (carried over unchanged from the standalone testing
project), the wizard page controllers, and the Curvature Project v4
integration point.

The `core/` modules and `config.py` were written and tested as standalone
files that import each other as top-level modules (e.g.
`from config import ...`, `from core.landmarks import ...`) rather than as
a sub-package. Rather than rewrite every internal import to use relative
imports (which would mean re-testing all of that already-validated logic),
this file adds this package's own directory to sys.path once, at import
time -- so `import config` and `from core import ...` continue to resolve
exactly as they did when we tested them standalone with main.py.
"""

import os
import sys

_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)
