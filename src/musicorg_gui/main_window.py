"""Top-level QMainWindow — hosts the QStackedWidget of screens.

Owns the screen-transition logic: WelcomeScreen → DashboardScreen once a
library is opened. The Dashboard's "Switch library…" action takes the
user back to Welcome.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QMainWindow, QStackedWidget

from musicorg import Config

from .screens import DashboardScreen, WelcomeScreen


class MainWindow(QMainWindow):
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("musicorg")
        self.resize(960, 640)

        self._stack = QStackedWidget(self)
        self.setCentralWidget(self._stack)

        self._welcome = WelcomeScreen(self)
        self._dashboard = DashboardScreen(self)

        self._stack.addWidget(self._welcome)
        self._stack.addWidget(self._dashboard)
        self._stack.setCurrentWidget(self._welcome)

        self._welcome.library_ready.connect(self._on_library_ready)
        # Dashboard's scan-re-run currently just refreshes the dashboard
        # after the Welcome screen handles the actual scan. The library
        # picker slice will turn this into a proper "re-scan in place" flow.

    @Slot(object)
    def _on_library_ready(self, cfg: Config) -> None:
        self._dashboard.set_library(cfg)
        self._stack.setCurrentWidget(self._dashboard)
        self.setWindowTitle(f"musicorg — {cfg.library_slug}")
