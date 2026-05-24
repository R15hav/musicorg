"""PyInstaller entry point.

``src/musicorg_gui/__main__.py`` uses a package-relative import so it
only works under ``python -m musicorg_gui``. PyInstaller invokes the
entry script directly, where relative imports raise. This launcher
imports the package absolutely and dispatches.
"""

from __future__ import annotations

import sys

from musicorg_gui import main


if __name__ == "__main__":
    sys.exit(main())
