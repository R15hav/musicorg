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

Visual language follows the design brief (Welcome / library picker
spec): a big serif heading and lede, a dashed drop-zone with a yellow
"Choose folder…" CTA, a grid of paper-surface library cards, and a
compact form for the new-library mode + country.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from musicorg import Config, ensure_state_dir, load_config

from .. import theme as t
from ..library_index import KnownLibrary, delete_library_state, list_known_libraries
from ..platform import state_root, symlink_supported
from ..workers import ApplyMode


def _relative_when(when: datetime) -> str:
    """Render a datetime as a brief "X ago" string used on library cards."""
    now = datetime.now()
    delta = now - when
    if delta < timedelta(seconds=60):
        return "just now"
    if delta < timedelta(minutes=60):
        m = int(delta.total_seconds() // 60)
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if delta < timedelta(hours=24):
        h = int(delta.total_seconds() // 3600)
        return f"{h} hour{'s' if h != 1 else ''} ago"
    if delta < timedelta(days=2):
        return "yesterday"
    if delta < timedelta(days=30):
        d = delta.days
        return f"{d} day{'s' if d != 1 else ''} ago"
    if delta < timedelta(days=365):
        months = max(int(delta.days // 30), 1)
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = max(int(delta.days // 365), 1)
    return f"{years} year{'s' if years != 1 else ''} ago"


class _LibraryCard(QFrame):
    """One card in the recent-libraries grid.

    Layout (per brief "Library card · Recent libraries"):
      serif slug · mono root path · two-row mini-table (Last scanned / Phases done)
    Card click opens the library. A small ✕ in the top-right deletes
    the library's state directory.
    """

    open_clicked = Signal(object)  # KnownLibrary
    delete_clicked = Signal(object)  # KnownLibrary

    def __init__(self, library: KnownLibrary, parent: Any = None) -> None:
        super().__init__(parent)
        self._library = library
        # Use the paper-surface property so the global QSS provides the
        # white background + light border + 16px radius.
        self.setProperty("surface", "paper")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(8)

        # Header row: serif slug + close ✕ aligned to the right.
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)

        slug = QLabel(library.slug)
        slug.setStyleSheet(
            f"QLabel {{ font-family: {t.SERIF}; font-size: 22px;"
            f" font-weight: 400; color: {t.TEXT_HIGH};"
            f" background: transparent; border: none; padding: 0; }}"
        )
        head.addWidget(slug, 1)

        delete_btn = QPushButton("✕")
        delete_btn.setFlat(True)
        delete_btn.setToolTip("Delete this library's state directory")
        delete_btn.setFixedSize(24, 24)
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none;"
            f" color: {t.TEXT_MEDIUM}; font-size: 14px; padding: 0; }}"
            f"QPushButton:hover {{ color: {t.ERROR_MAIN}; }}"
        )
        delete_btn.clicked.connect(lambda: self.delete_clicked.emit(library))
        head.addWidget(delete_btn, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(head)

        path = QLabel(str(library.root))
        path.setProperty("class", "mono")
        path.setStyleSheet(
            f"QLabel {{ font-family: {t.MONO}; font-size: 12px;"
            f" color: {t.TEXT_MEDIUM}; background: transparent; border: none;"
            f" padding: 0; }}"
        )
        path.setWordWrap(False)
        outer.addWidget(path)

        outer.addSpacing(6)

        # Mini stats table: Last scanned / Tracks / Phases done
        scanned_value = (
            f"{_relative_when(library.last_modified)} · "
            f"{library.track_count:,} track{'s' if library.track_count != 1 else ''}"
        )
        outer.addLayout(
            self._mini_row("Last scanned", scanned_value)
        )

        phases_done = "7 of 7" if library.organized else "in progress"
        outer.addLayout(
            self._mini_row("Phases done", phases_done, accent=library.organized)
        )

        # Make the whole card clickable (the ✕ button stops propagation
        # via QPushButton's own mouse handling, so this stays safe).
        self.mousePressEvent = self._on_press  # type: ignore[method-assign]

    def _mini_row(self, label: str, value: str, accent: bool = False) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        lbl = QLabel(label)
        lbl.setStyleSheet(
            f"QLabel {{ font-family: {t.SANS}; font-size: 13px;"
            f" color: {t.TEXT_MEDIUM}; background: transparent; border: none;"
            f" padding: 0; }}"
        )
        row.addWidget(lbl)
        row.addStretch(1)
        val = QLabel(value)
        color = t.SUCCESS_TEXT if accent else t.TEXT_HIGH
        val.setStyleSheet(
            f"QLabel {{ font-family: {t.SANS}; font-size: 13px;"
            f" color: {color}; background: transparent; border: none;"
            f" padding: 0; }}"
        )
        row.addWidget(val)
        return row

    def _on_press(self, event: Any) -> None:
        # Left-click anywhere on the card opens the library.
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_clicked.emit(self._library)
        event.accept()


class _DropZone(QFrame):
    """Dashed-border drop zone with a centered title + primary CTA.

    Acts as the visual anchor for "pick a folder to start a new run."
    Hosts the "Choose folder…" button; clicking it opens a native
    folder dialog. The screen owns the actual handler so dropped paths
    feed the same code path.
    """

    folder_chosen = Signal(str)

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setStyleSheet(
            f"#dropZone {{ background: {t.SURFACE_PAPER};"
            f" border: 1.5px dashed {t.BORDER_MEDIUM};"
            f" border-radius: 16px; }}"
        )
        self.setAcceptDrops(True)
        self.setMinimumHeight(160)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 28, 24, 28)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Drop a music folder here, or")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"QLabel {{ font-family: {t.SERIF}; font-size: 22px;"
            f" font-weight: 400; color: {t.TEXT_HIGH};"
            f" background: transparent; border: none; }}"
        )
        layout.addWidget(title)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.addStretch(1)
        self._choose_btn = QPushButton("Choose folder…")
        self._choose_btn.setProperty("variant", "primary")
        self._choose_btn.setMinimumWidth(160)
        self._choose_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._choose_btn.clicked.connect(self._on_browse)
        btn_row.addWidget(self._choose_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

    @Slot()
    def _on_browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select music folder")
        if folder:
            self.folder_chosen.emit(folder)

    # Drag-and-drop ---------------------------------------------------------

    def dragEnterEvent(self, event: Any) -> None:  # noqa: N802 — Qt API
        if event.mimeData().hasUrls():
            self.setStyleSheet(
                f"#dropZone {{ background: {t.PRIMARY_50};"
                f" border: 1.5px dashed {t.PRIMARY_500};"
                f" border-radius: 16px; }}"
            )
            event.acceptProposedAction()

    def dragLeaveEvent(self, event: Any) -> None:  # noqa: N802 — Qt API
        self.setStyleSheet(
            f"#dropZone {{ background: {t.SURFACE_PAPER};"
            f" border: 1.5px dashed {t.BORDER_MEDIUM};"
            f" border-radius: 16px; }}"
        )

    def dropEvent(self, event: Any) -> None:  # noqa: N802 — Qt API
        self.dragLeaveEvent(event)
        urls = event.mimeData().urls()
        for url in urls:
            local = url.toLocalFile()
            if local and Path(local).is_dir():
                self.folder_chosen.emit(local)
                event.acceptProposedAction()
                return


class WelcomeScreen(QWidget):
    """Pick an existing library OR configure a new organize run."""

    start_requested = Signal(object, object, str)  # (Config, Path root, mode)
    open_existing = Signal(object)  # KnownLibrary

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)

        # Scroll the entire welcome surface — small screens still see
        # both the drop-zone and the recent grid without truncation.
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root_layout.addWidget(scroll)

        page = QWidget()
        scroll.setWidget(page)

        outer = QVBoxLayout(page)
        outer.setContentsMargins(64, 40, 64, 40)
        outer.setSpacing(18)

        # Heading -----------------------------------------------------------
        title = QLabel("Open a music library.")
        title.setProperty("class", "h1")
        outer.addWidget(title)

        lede = QLabel(
            "A library is just a folder of audio files. musicorg keeps its records "
            "next to it, in your state directory."
        )
        lede.setProperty("class", "muted")
        lede.setWordWrap(True)
        outer.addWidget(lede)

        outer.addSpacing(8)

        # Drop zone ---------------------------------------------------------
        self._drop_zone = _DropZone()
        self._drop_zone.folder_chosen.connect(self._on_folder_chosen)
        outer.addWidget(self._drop_zone)

        # New-run form ------------------------------------------------------
        outer.addSpacing(16)

        form_eyebrow = QLabel("New library defaults")
        form_eyebrow.setProperty("class", "footnote")
        outer.addWidget(form_eyebrow)

        form_card = QFrame()
        form_card.setProperty("surface", "paper")
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(20, 18, 20, 18)
        form_layout.setSpacing(12)

        form = QFormLayout()
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(18)
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

        mode_label = QLabel("Apply mode")
        mode_label.setProperty("class", "body")
        form.addRow(mode_label, mode_box)
        form_layout.addLayout(form)

        # Pending folder display + Start button
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 4, 0, 0)
        bottom_row.setSpacing(12)
        self._pending_label = QLabel("Choose a folder to start.")
        self._pending_label.setProperty("class", "muted")
        self._pending_label.setWordWrap(True)
        bottom_row.addWidget(self._pending_label, 1)

        self._start_btn = QPushButton("Start →")
        self._start_btn.setProperty("variant", "primary")
        self._start_btn.setMinimumWidth(140)
        self._start_btn.setEnabled(False)
        self._start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start_btn.clicked.connect(self._on_start)
        bottom_row.addWidget(self._start_btn)
        form_layout.addLayout(bottom_row)

        outer.addWidget(form_card)

        # Recent libraries (last section) -----------------------------------
        outer.addSpacing(16)
        self._recent_eyebrow = QLabel("Recent libraries")
        self._recent_eyebrow.setProperty("class", "footnote")
        outer.addWidget(self._recent_eyebrow)

        self._recent_grid_host = QWidget()
        self._recent_grid = QGridLayout(self._recent_grid_host)
        self._recent_grid.setContentsMargins(0, 0, 0, 0)
        self._recent_grid.setHorizontalSpacing(16)
        self._recent_grid.setVerticalSpacing(16)
        outer.addWidget(self._recent_grid_host)

        self._recent_empty = QLabel(
            "No libraries yet — drop a folder above to start your first run."
        )
        self._recent_empty.setProperty("class", "muted")
        outer.addWidget(self._recent_empty)

        outer.addStretch(1)

        # Pending folder state — set by drop-zone / folder dialog.
        self._pending_folder: Path | None = None
        self._refresh_recent()

    @staticmethod
    def _available_modes() -> list[tuple[str, str]]:
        modes = [("move", "Move"), ("copy", "Copy")]
        if sys.platform != "win32" or symlink_supported():
            modes.append(("symlink", "Symlink"))
        return modes

    def reset(self) -> None:
        self._pending_folder = None
        self._pending_label.setText("Choose a folder to start.")
        self._pending_label.setProperty("class", "muted")
        self._pending_label.style().unpolish(self._pending_label)
        self._pending_label.style().polish(self._pending_label)
        for btn in self._mode_group.buttons():
            if btn.property("value") == "move":
                btn.setChecked(True)
                break
        self._start_btn.setEnabled(False)
        self._refresh_recent()

    def _refresh_recent(self) -> None:
        # Clear existing grid items
        while self._recent_grid.count():
            item = self._recent_grid.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        libraries = list_known_libraries(state_root())
        if not libraries:
            self._recent_grid_host.setVisible(False)
            self._recent_eyebrow.setVisible(False)
            self._recent_empty.setVisible(True)
            return

        self._recent_empty.setVisible(False)
        self._recent_eyebrow.setVisible(True)
        self._recent_grid_host.setVisible(True)

        # Two-column grid per brief mock-up.
        cols = 2
        for idx, lib in enumerate(libraries):
            card = _LibraryCard(lib)
            card.open_clicked.connect(self._on_open_clicked)
            card.delete_clicked.connect(self._on_delete_clicked)
            row, col = divmod(idx, cols)
            self._recent_grid.addWidget(card, row, col)

    @Slot(str)
    def _on_folder_chosen(self, folder: str) -> None:
        path = Path(folder)
        if not path.is_dir():
            return
        self._pending_folder = path
        self._pending_label.setText(f"Folder selected:  {path}")
        self._start_btn.setEnabled(True)

    def _selected_mode(self) -> ApplyMode:
        for btn in self._mode_group.buttons():
            if btn.isChecked():
                return btn.property("value")  # type: ignore[no-any-return]
        return "move"

    @Slot()
    def _on_start(self) -> None:
        if self._pending_folder is None:
            return
        root = self._pending_folder.resolve()
        if not root.is_dir():
            return
        cfg = load_config(root=root, state_root=state_root())
        # default_country is the fallback for songs detect_country() can't
        # resolve even after the metadata phase canonicalises tags. The user
        # no longer picks one — bollywood stays as a benign fallback that
        # matches detect_country's own default.
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
