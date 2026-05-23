"""Top-level QMainWindow — hosts the QStackedWidget of screens.

For the scaffold this is little more than a host for ``WelcomeScreen``.
v0.3-gui will extend it with: library picker on launch, dashboard with
per-phase status cards, stage screens, settings, gamdl setup, undo
browser, and a log-view toggle on the status panel.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QMainWindow, QStackedWidget

from .screens import WelcomeScreen


class MainWindow(QMainWindow):
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("musicorg")
        self.resize(960, 640)

        self._stack = QStackedWidget(self)
        self.setCentralWidget(self._stack)

        self._welcome = WelcomeScreen(self)
        self._stack.addWidget(self._welcome)
        self._stack.setCurrentWidget(self._welcome)
