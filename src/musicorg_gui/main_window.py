"""Top-level QMainWindow — hosts the WelcomeScreen → PipelineScreen → CompletionScreen flow.

The auto-pipeline model: once the user picks a folder + mode on Welcome,
Pipeline runs Stage 1 unattended, pausing only for plan-preview
approval. Pipeline emits ``completed`` → MainWindow shows Completion.
From Completion, "Organize another library" returns to Welcome;
Metadata / Upgrade / Undo are separate functions (stubs in v0.3-gui).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QMainWindow, QStackedWidget

from musicorg import Config

from .screens import CompletionScreen, PipelineScreen, WelcomeScreen
from .workers import ApplyMode


class MainWindow(QMainWindow):
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("musicorg")
        self.resize(1000, 680)

        self._active_cfg: Config | None = None

        self._stack = QStackedWidget(self)
        self.setCentralWidget(self._stack)

        self._welcome = WelcomeScreen(self)
        self._pipeline = PipelineScreen(self)
        self._completion = CompletionScreen(self)

        for screen in (self._welcome, self._pipeline, self._completion):
            self._stack.addWidget(screen)
        self._stack.setCurrentWidget(self._welcome)

        self._welcome.start_requested.connect(self._on_start_requested)
        self._pipeline.completed.connect(self._on_pipeline_completed)
        self._pipeline.cancelled.connect(self._go_to_welcome)
        self._pipeline.failed_out.connect(lambda _msg: None)  # handled inside pipeline; nav stays
        self._completion.restart_requested.connect(self._go_to_welcome)

    @Slot(object, object, str)
    def _on_start_requested(self, cfg: Config, root: Path, mode: str) -> None:
        self._active_cfg = cfg
        self.setWindowTitle(f"musicorg — {cfg.library_slug}")
        self._stack.setCurrentWidget(self._pipeline)
        self._pipeline.start(cfg, root, mode)  # type: ignore[arg-type]

    @Slot(object)
    def _on_pipeline_completed(self, stats: dict) -> None:
        if self._active_cfg is None:
            return
        self._completion.show_for(self._active_cfg, stats)
        self._stack.setCurrentWidget(self._completion)

    def _go_to_welcome(self) -> None:
        self.setWindowTitle("musicorg")
        self._welcome.reset()
        self._stack.setCurrentWidget(self._welcome)
        self._active_cfg = None
