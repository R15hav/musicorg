"""QApplication entry point.

Builds a QApplication, applies cross-platform defaults (high-DPI, app
metadata, organization name so QSettings lands in the right place per
OS), creates the MainWindow, and runs the event loop. Called from
``musicorg_gui.main()`` and ``python -m musicorg_gui``.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from . import __version__
from .main_window import MainWindow


def run() -> int:
    # High-DPI handling is on by default in Qt 6, but enabling rounded
    # scaling avoids a known blurry-fonts issue on fractional-scale Linux
    # desktops (1.25x, 1.5x).
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("musicorg")
    app.setApplicationDisplayName("musicorg")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("musicorg")
    app.setOrganizationDomain("musicorg.local")

    window = MainWindow()
    window.show()

    return app.exec()
