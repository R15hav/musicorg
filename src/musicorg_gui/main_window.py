"""Top-level QMainWindow — TopBar + QStackedWidget of screens.

The TopBar is persistent across all screens (per design brief P02:
"Undo is felt, not documented" — same chip in the same place
everywhere). The stack hosts: WelcomeScreen → PipelineScreen →
CompletionScreen, with side detours to UndoScreen, MetadataScreen,
GamdlSetupScreen, UpgradeScreen reached from Completion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from musicorg import Config

from .library_index import KnownLibrary
from .platform import state_root
from .screens import (
    CompletionScreen,
    GamdlSetupScreen,
    MetadataScreen,
    PipelineScreen,
    UndoScreen,
    UpgradeScreen,
    WelcomeScreen,
)
from .widgets import TopBar
from .workers import ApplyMode


class MainWindow(QMainWindow):
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("musicorg")
        self.resize(1080, 720)

        self._active_cfg: Config | None = None

        # Central widget hosts TopBar + QStackedWidget.
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._topbar = TopBar()
        outer.addWidget(self._topbar)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)

        self.setCentralWidget(central)

        self._welcome = WelcomeScreen(self)
        self._pipeline = PipelineScreen(self)
        self._completion = CompletionScreen(self)
        self._undo = UndoScreen(self)
        self._metadata = MetadataScreen(self)
        self._gamdl_setup = GamdlSetupScreen(self)
        self._upgrade = UpgradeScreen(self)

        for screen in (
            self._welcome,
            self._pipeline,
            self._completion,
            self._undo,
            self._metadata,
            self._gamdl_setup,
            self._upgrade,
        ):
            self._stack.addWidget(screen)
        self._stack.setCurrentWidget(self._welcome)

        # Screen → MainWindow wiring
        self._welcome.start_requested.connect(self._on_start_requested)
        self._welcome.open_existing.connect(self._on_open_existing)
        self._pipeline.completed.connect(self._on_pipeline_completed)
        self._pipeline.cancelled.connect(self._go_to_welcome)
        self._pipeline.failed_out.connect(lambda _msg: None)
        self._completion.restart_requested.connect(self._go_to_welcome)
        self._completion.undo_requested.connect(self._show_undo)
        self._completion.metadata_requested.connect(self._show_metadata)
        self._completion.upgrade_requested.connect(self._show_upgrade)
        self._undo.back_requested.connect(self._show_completion)
        self._metadata.back_requested.connect(self._show_completion)
        self._upgrade.back_requested.connect(self._show_completion)
        self._upgrade.setup_requested.connect(self._show_gamdl_setup)
        self._gamdl_setup.back_requested.connect(self._show_upgrade)
        self._gamdl_setup.proceed_requested.connect(self._show_upgrade)

        # TopBar wiring
        self._topbar.library_clicked.connect(self._go_to_welcome)
        self._topbar.undo_clicked.connect(self._show_undo_from_topbar)
        self._topbar.settings_clicked.connect(self._on_settings_clicked)

    # ─── Screen lifecycle helpers ─────────────────────────────────────

    def _set_active_library(self, cfg: Config | None) -> None:
        self._active_cfg = cfg
        if cfg is None:
            self.setWindowTitle("musicorg")
            self._topbar.set_library(None)
            self._topbar.set_undo(available=False)
            return
        self.setWindowTitle(f"musicorg — {cfg.library_slug}")
        self._topbar.set_library(cfg.library_slug, cfg.library_root)
        self._refresh_undo_chip()

    def _refresh_undo_chip(self) -> None:
        if self._active_cfg is None:
            self._topbar.set_undo(available=False)
            return
        state_dir = Path(self._active_cfg.state_dir)
        undo_scripts = list(state_dir.glob("undo_*.sh"))
        if undo_scripts:
            newest = max(undo_scripts, key=lambda p: p.stat().st_mtime)
            self._topbar.set_undo(available=True, label=f"Undo · {newest.stem.split('_', 1)[-1]}")
        else:
            self._topbar.set_undo(available=False)

    # ─── Slots ────────────────────────────────────────────────────────

    @Slot(object, object, str)
    def _on_start_requested(self, cfg: Config, root: Path, mode: str) -> None:
        self._set_active_library(cfg)
        self._stack.setCurrentWidget(self._pipeline)
        self._pipeline.start(cfg, root, mode)  # type: ignore[arg-type]

    @Slot(object)
    def _on_open_existing(self, library: KnownLibrary) -> None:
        from musicorg import load_config

        cfg = load_config(library=library.slug, root=library.root, state_root=state_root())
        cfg.default_country = library.default_country  # type: ignore[assignment]
        cfg.apply_mode = library.apply_mode  # type: ignore[assignment]
        self._set_active_library(cfg)

        if library.organized:
            self._completion.show_existing(cfg)
            self._stack.setCurrentWidget(self._completion)
        else:
            self._stack.setCurrentWidget(self._pipeline)
            self._pipeline.start(cfg, library.root, library.apply_mode)  # type: ignore[arg-type]

    @Slot(object)
    def _on_pipeline_completed(self, stats: dict) -> None:
        if self._active_cfg is None:
            return
        self._refresh_undo_chip()
        self._completion.show_for(self._active_cfg, stats)
        self._stack.setCurrentWidget(self._completion)

    def _go_to_welcome(self) -> None:
        self._set_active_library(None)
        self._welcome.reset()
        self._stack.setCurrentWidget(self._welcome)

    def _show_undo(self) -> None:
        if self._active_cfg is None:
            return
        self._undo.show_for(self._active_cfg)
        self._stack.setCurrentWidget(self._undo)

    def _show_undo_from_topbar(self) -> None:
        if self._active_cfg is None:
            return
        self._show_undo()

    def _show_completion(self) -> None:
        self._refresh_undo_chip()
        self._stack.setCurrentWidget(self._completion)

    def _show_metadata(self) -> None:
        if self._active_cfg is None:
            return
        self._stack.setCurrentWidget(self._metadata)
        self._metadata.show_for(self._active_cfg)

    def _show_upgrade(self) -> None:
        if self._active_cfg is None:
            return
        self._stack.setCurrentWidget(self._upgrade)
        self._upgrade.show_for(self._active_cfg)

    def _show_gamdl_setup(self) -> None:
        if self._active_cfg is None:
            return
        self._stack.setCurrentWidget(self._gamdl_setup)
        self._gamdl_setup.show_for(self._active_cfg)

    def _on_settings_clicked(self) -> None:
        # Settings dialog is a future slice; for now this is a no-op.
        pass
