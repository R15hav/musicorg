"""Top-level QMainWindow — hosts the QStackedWidget of screens.

Owns the screen-transition logic: WelcomeScreen → DashboardScreen once a
library is opened, then between Dashboard and per-phase screens as the
user runs each stage. Also owns the active library Config so phase
screens don't have to thread it through their own state.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QMainWindow, QStackedWidget

from musicorg import Config

from .screens import DashboardScreen, DedupeResultsScreen, WelcomeScreen


class MainWindow(QMainWindow):
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("musicorg")
        self.resize(960, 640)

        self._active_cfg: Config | None = None

        self._stack = QStackedWidget(self)
        self.setCentralWidget(self._stack)

        self._welcome = WelcomeScreen(self)
        self._dashboard = DashboardScreen(self)
        self._dedupe_results = DedupeResultsScreen(self)

        for screen in (self._welcome, self._dashboard, self._dedupe_results):
            self._stack.addWidget(screen)
        self._stack.setCurrentWidget(self._welcome)

        self._welcome.library_ready.connect(self._on_library_ready)
        self._dashboard.run_requested.connect(self._on_run_requested)
        self._dedupe_results.back_requested.connect(self._show_dashboard)

    @Slot(object)
    def _on_library_ready(self, cfg: Config) -> None:
        self._active_cfg = cfg
        self._show_dashboard()

    def _show_dashboard(self) -> None:
        if self._active_cfg is None:
            return
        self._dashboard.set_library(self._active_cfg)
        self._stack.setCurrentWidget(self._dashboard)
        self.setWindowTitle(f"musicorg — {self._active_cfg.library_slug}")

    @Slot(str)
    def _on_run_requested(self, phase_key: str) -> None:
        if self._active_cfg is None:
            return
        if phase_key == "dedupe":
            self._stack.setCurrentWidget(self._dedupe_results)
            self._dedupe_results.start(self._active_cfg)
