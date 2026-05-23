"""Welcome screen — configure the organize run before kicking off Stage 1.

The user picks: music folder, apply mode (move / copy / symlink), and
default country routing. Clicking **Start →** emits
``start_requested(cfg, root, mode)`` and the MainWindow transitions to
the PipelineScreen which auto-runs the rest of Stage 1.

Symlink mode is hidden on Windows by default — see
``platform.symlink_supported``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from musicorg import Config, ensure_state_dir, load_config

from ..platform import state_root, symlink_supported
from ..workers import ApplyMode


_COUNTRIES = [
    ("bollywood", "Bollywood"),
    ("hollywood", "Hollywood"),
    ("unknown", "Unknown"),
]


class WelcomeScreen(QWidget):
    """Collect organize-run config; emit start_requested when user clicks Start."""

    start_requested = Signal(object, object, str)  # (Config, Path root, mode)

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 40, 48, 40)
        outer.setSpacing(18)

        title = QLabel("musicorg")
        title.setStyleSheet("font-size: 28px; font-weight: 600;")
        outer.addWidget(title)

        subtitle = QLabel(
            "Pick a music folder and how to apply the organize plan. "
            "Stage 1 will scan, deduplicate, plan a tidy tree, then pause once "
            "for you to approve the move plan before any file is touched."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: palette(mid);")
        outer.addWidget(subtitle)

        # Folder picker -------------------------------------------------------
        folder_label = QLabel("Music folder")
        folder_label.setStyleSheet("font-weight: 600; margin-top: 8px;")
        outer.addWidget(folder_label)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self._folder_edit = QLineEdit()
        self._folder_edit.setPlaceholderText("/path/to/your/music")
        self._folder_edit.textChanged.connect(self._update_start_state)
        folder_row.addWidget(self._folder_edit, 1)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse)
        folder_row.addWidget(browse_btn)
        outer.addLayout(folder_row)

        # Settings form -------------------------------------------------------
        form_label = QLabel("Settings")
        form_label.setStyleSheet("font-weight: 600; margin-top: 16px;")
        outer.addWidget(form_label)

        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(8)

        # Apply mode (radio row)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(20)
        self._mode_group = QButtonGroup(self)
        for value, label in self._available_modes():
            rb = QRadioButton(label)
            rb.setProperty("value", value)
            self._mode_group.addButton(rb)
            mode_row.addWidget(rb)
            if value == "move":
                rb.setChecked(True)
        mode_row.addStretch(1)
        mode_box = QWidget()
        mode_box.setLayout(mode_row)
        form.addRow("Apply mode:", mode_box)

        # Default country dropdown
        self._country_combo = QComboBox()
        for value, label in _COUNTRIES:
            self._country_combo.addItem(label, userData=value)
        form.addRow("Default country:", self._country_combo)

        outer.addLayout(form)

        outer.addStretch(1)

        # Start row -----------------------------------------------------------
        start_row = QHBoxLayout()
        start_row.addStretch(1)
        self._start_btn = QPushButton("Start →")
        self._start_btn.setMinimumWidth(160)
        self._start_btn.setStyleSheet("padding: 8px 16px; font-weight: 600;")
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start)
        start_row.addWidget(self._start_btn)
        outer.addLayout(start_row)

    @staticmethod
    def _available_modes() -> list[tuple[str, str]]:
        modes = [("move", "Move"), ("copy", "Copy")]
        if sys.platform != "win32" or symlink_supported():
            modes.append(("symlink", "Symlink"))
        return modes

    def reset(self) -> None:
        self._folder_edit.clear()
        for btn in self._mode_group.buttons():
            if btn.property("value") == "move":
                btn.setChecked(True)
                break
        self._country_combo.setCurrentIndex(0)
        self._update_start_state()

    def _update_start_state(self) -> None:
        folder = self._folder_edit.text().strip()
        self._start_btn.setEnabled(bool(folder) and Path(folder).is_dir())

    @Slot()
    def _on_browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select music folder")
        if folder:
            self._folder_edit.setText(folder)

    def _selected_mode(self) -> ApplyMode:
        for btn in self._mode_group.buttons():
            if btn.isChecked():
                return btn.property("value")  # type: ignore[no-any-return]
        return "move"

    @Slot()
    def _on_start(self) -> None:
        root = Path(self._folder_edit.text().strip()).resolve()
        if not root.is_dir():
            return
        country = self._country_combo.currentData() or "bollywood"
        cfg = load_config(root=root, state_root=state_root())
        cfg.default_country = country
        cfg.apply_mode = self._selected_mode()
        ensure_state_dir(cfg)
        self.start_requested.emit(cfg, root, self._selected_mode())
