"""Welcome screen — recent-libraries picker + new-library config form.

Two paths:

- **Open an existing library** — clicking a recent-library card emits
  ``open_existing(cfg)``. MainWindow checks whether the library has
  been organized (an undo_<TS>.sh exists) and either jumps to
  Completion or to a fresh Pipeline run.
- **Start a new run** — folder picker + mode + country + Start →
  emits ``start_requested(cfg, root, mode)``.

The recent-library list reads ``state_root()`` directly via
``library_index.list_known_libraries``; nothing is cached in this
screen. Calling ``reset()`` re-reads the list, so the picker stays
current after a deletion or a fresh organize run.
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
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from musicorg import Config, ensure_state_dir, load_config

from ..library_index import KnownLibrary, delete_library_state, list_known_libraries
from ..platform import state_root, symlink_supported
from ..workers import ApplyMode


_COUNTRIES = [
    ("bollywood", "Bollywood"),
    ("hollywood", "Hollywood"),
    ("unknown", "Unknown"),
]


class _LibraryCard(QFrame):
    """One row in the recent-libraries list."""

    open_clicked = Signal(object)  # KnownLibrary
    delete_clicked = Signal(object)  # KnownLibrary

    def __init__(self, library: KnownLibrary, parent: Any = None) -> None:
        super().__init__(parent)
        self._library = library
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { border: 1px solid palette(mid); border-radius: 6px;"
            " padding: 10px; }"
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)

        text = QVBoxLayout()
        text.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel(library.slug)
        title.setStyleSheet("font-weight: 600; border: none; padding: 0;")
        title_row.addWidget(title)

        status_pill = QLabel(library.status_label)
        status_color = "#2e7d32" if library.organized else "#a16207"
        status_pill.setStyleSheet(
            f"QLabel {{ background: {status_color}; color: white;"
            f" border-radius: 8px; padding: 1px 8px; font-size: 10px; border: none; }}"
        )
        title_row.addWidget(status_pill)
        title_row.addStretch(1)
        text.addLayout(title_row)

        sub = QLabel(library.display_subtitle)
        sub.setStyleSheet("color: palette(mid); border: none; padding: 0;")
        text.addWidget(sub)
        row.addLayout(text, 1)

        open_btn = QPushButton("Open")
        open_btn.clicked.connect(lambda: self.open_clicked.emit(library))
        row.addWidget(open_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        delete_btn = QPushButton("✕")
        delete_btn.setFlat(True)
        delete_btn.setToolTip("Delete this library's state directory")
        delete_btn.setFixedWidth(28)
        delete_btn.setStyleSheet("padding: 0; font-size: 16px;")
        delete_btn.clicked.connect(lambda: self.delete_clicked.emit(library))
        row.addWidget(delete_btn, alignment=Qt.AlignmentFlag.AlignVCenter)


class WelcomeScreen(QWidget):
    """Pick an existing library OR configure a new organize run."""

    start_requested = Signal(object, object, str)  # (Config, Path root, mode)
    open_existing = Signal(object)  # KnownLibrary

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 32, 48, 32)
        outer.setSpacing(14)

        title = QLabel("musicorg")
        title.setStyleSheet("font-size: 28px; font-weight: 600;")
        outer.addWidget(title)

        subtitle = QLabel(
            "Open a library you've worked on before, or pick a folder to start "
            "a new organize run."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: palette(mid);")
        outer.addWidget(subtitle)

        # Recent libraries section -------------------------------------------
        self._recent_header = QLabel("Recent libraries")
        self._recent_header.setStyleSheet("font-weight: 600; margin-top: 10px;")
        outer.addWidget(self._recent_header)

        self._recent_scroll = QScrollArea()
        self._recent_scroll.setWidgetResizable(True)
        self._recent_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._recent_scroll.setMaximumHeight(220)
        self._recent_inner = QWidget()
        self._recent_layout = QVBoxLayout(self._recent_inner)
        self._recent_layout.setContentsMargins(0, 0, 0, 0)
        self._recent_layout.setSpacing(8)
        self._recent_scroll.setWidget(self._recent_inner)
        outer.addWidget(self._recent_scroll)

        self._recent_empty = QLabel("No libraries yet — start one below.")
        self._recent_empty.setStyleSheet("color: palette(mid); padding: 8px;")
        outer.addWidget(self._recent_empty)

        # Separator
        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setStyleSheet("color: palette(mid); margin-top: 6px;")
        outer.addWidget(rule)

        # New-library section -----------------------------------------------
        new_header = QLabel("Start a new run")
        new_header.setStyleSheet("font-weight: 600;")
        outer.addWidget(new_header)

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

        form = QFormLayout()
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(8)

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

        self._country_combo = QComboBox()
        for value, label in _COUNTRIES:
            self._country_combo.addItem(label, userData=value)
        form.addRow("Default country:", self._country_combo)
        outer.addLayout(form)

        outer.addStretch(1)

        start_row = QHBoxLayout()
        start_row.addStretch(1)
        self._start_btn = QPushButton("Start →")
        self._start_btn.setMinimumWidth(160)
        self._start_btn.setStyleSheet("padding: 8px 16px; font-weight: 600;")
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start)
        start_row.addWidget(self._start_btn)
        outer.addLayout(start_row)

        self._refresh_recent()

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
        self._refresh_recent()

    def _refresh_recent(self) -> None:
        # Clear existing cards
        while self._recent_layout.count():
            item = self._recent_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        libraries = list_known_libraries(state_root())
        if not libraries:
            self._recent_scroll.setVisible(False)
            self._recent_header.setVisible(False)
            self._recent_empty.setVisible(True)
            return

        self._recent_empty.setVisible(False)
        self._recent_header.setVisible(True)
        self._recent_scroll.setVisible(True)

        for lib in libraries:
            card = _LibraryCard(lib)
            card.open_clicked.connect(self._on_open_clicked)
            card.delete_clicked.connect(self._on_delete_clicked)
            self._recent_layout.addWidget(card)
        self._recent_layout.addStretch(1)

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

    @Slot(object)
    def _on_open_clicked(self, library: KnownLibrary) -> None:
        if not library.root.exists():
            QMessageBox.warning(
                self,
                "Library root missing",
                f"The folder this library was created from is gone:\n\n"
                f"  {library.root}\n\n"
                "Reconnect or move the folder back, then try again. The "
                "state directory itself is still intact at:\n\n"
                f"  {library.state_dir}",
            )
            return
        self.open_existing.emit(library)

    @Slot(object)
    def _on_delete_clicked(self, library: KnownLibrary) -> None:
        answer = QMessageBox.question(
            self,
            "Delete library state?",
            f"This removes the state directory for '{library.slug}':\n\n"
            f"  {library.state_dir}\n\n"
            "Your music files at the library root are NOT touched. Any "
            "undo scripts in the state directory will be deleted too.\n\n"
            "Proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_library_state(library)
        except OSError as exc:
            QMessageBox.warning(self, "Delete failed", str(exc))
            return
        self._refresh_recent()
