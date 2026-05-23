"""UndoScreen — list and run undo_<TS>.sh scripts for the active library.

Lists every ``undo_*.sh`` in ``<state_dir>/`` sorted newest-first, with
its timestamp parsed from the filename and a destructive-action button
guarded by a confirmation dialog. Stage 1 ``execute_plan`` is the only
phase that writes these in v0.3-gui; Stage 2 / 3 will add tag-snapshot
undos (``undo_phase*.py`` + ``tag_snapshot_*.json``) which arrive in a
later slice.

Pattern matches the rest of the GUI: invisible status until needed, one
clear approval gate. Running an undo is the gate — the destructive
confirmation dialog. Everything after is just status.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from musicorg import Config, ProgressEvent

from ..widgets import StatusPanel
from ..workers import UndoWorker


_TS_RX = re.compile(r"undo_(\d{8})_(\d{6})\.sh$")


@dataclass(frozen=True)
class _UndoEntry:
    path: Path
    timestamp: datetime | None
    size_bytes: int

    @property
    def display_time(self) -> str:
        if self.timestamp:
            return self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        return "(unknown time)"

    @property
    def display_size(self) -> str:
        n = self.size_bytes
        for unit in ("B", "KB", "MB"):
            if n < 1024:
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} GB"


def _list_entries(state_dir: Path) -> list[_UndoEntry]:
    entries: list[_UndoEntry] = []
    for path in state_dir.glob("undo_*.sh"):
        m = _TS_RX.search(path.name)
        ts: datetime | None = None
        if m:
            try:
                ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            except ValueError:
                ts = None
        if ts is None:
            try:
                ts = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                ts = None
        size = path.stat().st_size if path.exists() else 0
        entries.append(_UndoEntry(path=path, timestamp=ts, size_bytes=size))
    entries.sort(key=lambda e: e.timestamp or datetime.min, reverse=True)
    return entries


class _UndoRow(QFrame):
    """One undo entry, with a Run undo button."""

    run_clicked = Signal(object)  # _UndoEntry

    def __init__(self, entry: _UndoEntry, parent: Any = None) -> None:
        super().__init__(parent)
        self._entry = entry
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { border: 1px solid palette(mid); border-radius: 6px;"
            " padding: 10px; }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(16)

        text = QVBoxLayout()
        text.setSpacing(2)

        title = QLabel(entry.display_time)
        title.setStyleSheet("font-weight: 600; border: none; padding: 0;")
        text.addWidget(title)

        sub = QLabel(f"{entry.path.name}  ·  {entry.display_size}")
        sub.setStyleSheet("color: palette(mid); border: none; padding: 0;")
        text.addWidget(sub)

        row.addLayout(text, 1)

        self._btn = QPushButton("Run undo…")
        self._btn.clicked.connect(lambda: self.run_clicked.emit(entry))
        row.addWidget(self._btn, alignment=Qt.AlignmentFlag.AlignVCenter)

    def set_running(self, running: bool) -> None:
        self._btn.setEnabled(not running)
        self._btn.setText("Running…" if running else "Run undo…")


class _UndoListPane(QWidget):
    """List view of undo entries."""

    run_requested = Signal(object)  # _UndoEntry
    back_requested = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._title = QLabel("Undo history")
        self._title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(self._title)

        self._caption = QLabel("")
        self._caption.setWordWrap(True)
        self._caption.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._caption)

        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(8)
        layout.addWidget(self._rows_container, 1)

        self._empty = QLabel("No undo scripts in this library yet.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet("color: palette(mid); padding: 32px;")
        self._empty.setVisible(False)
        layout.addWidget(self._empty)

        actions = QHBoxLayout()
        back_btn = QPushButton("← Back")
        back_btn.clicked.connect(self.back_requested)
        actions.addWidget(back_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._rows: list[_UndoRow] = []

    def populate(self, entries: list[_UndoEntry], library_slug: str) -> None:
        self._title.setText(f"Undo history — {library_slug}")
        # Clear existing rows
        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

        if not entries:
            self._caption.setVisible(False)
            self._empty.setVisible(True)
            return

        self._empty.setVisible(False)
        self._caption.setVisible(True)
        self._caption.setText(
            f"{len(entries)} undo script{'s' if len(entries) != 1 else ''} available. "
            "Newest first. Running an undo is irreversible — it reverses the file "
            "moves from that run."
        )
        for entry in entries:
            row = _UndoRow(entry)
            row.run_clicked.connect(self.run_requested)
            self._rows_layout.addWidget(row)
            self._rows.append(row)

    def set_running(self, target: _UndoEntry | None) -> None:
        for row in self._rows:
            row.set_running(row._entry == target)


class _UndoRunningPane(QWidget):
    """Pane shown while one undo is in flight."""

    done_clicked = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._title = QLabel("Running undo…")
        self._title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(self._title)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet("color: palette(mid);")
        self._summary.setVisible(False)
        layout.addWidget(self._summary)

        self.status = StatusPanel()
        layout.addWidget(self.status, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._done_btn = QPushButton("Done")
        self._done_btn.setVisible(False)
        self._done_btn.clicked.connect(self.done_clicked)
        actions.addWidget(self._done_btn)
        layout.addLayout(actions)

    def start(self, entry: _UndoEntry) -> None:
        self._title.setText(f"Running undo — {entry.display_time}")
        self._summary.setVisible(False)
        self.status.reset()
        self.status.push(ProgressEvent(
            phase="undo", current=0, total=0, path=str(entry.path),
            message=f"Starting {entry.path.name}…",
        ))
        self._done_btn.setVisible(False)

    def finish(self, rc: int) -> None:
        self.status.set_progress_visible(False)
        if rc == 0:
            self._title.setText("Undo complete")
            self._summary.setText(
                "Files were moved back to their original locations. The undo "
                "script ran without errors."
            )
        else:
            self._title.setText("Undo finished with errors")
            self._summary.setText(
                f"The undo script returned exit code {rc}. Some files may not "
                "have been reverted. Check the log view above for details."
            )
        self._summary.setVisible(True)
        self._done_btn.setVisible(True)
        self._done_btn.setFocus()

    def set_failure_message(self, message: str) -> None:
        self.status.set_progress_visible(False)
        self._title.setText("Undo failed to start")
        self._summary.setText(message)
        self._summary.setVisible(True)
        self._done_btn.setVisible(True)


class UndoScreen(QWidget):
    back_requested = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg: Config | None = None
        self._worker: UndoWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 32, 48, 32)
        outer.setSpacing(16)

        self._stack = QStackedWidget(self)
        self._list_pane = _UndoListPane()
        self._running_pane = _UndoRunningPane()
        self._stack.addWidget(self._list_pane)
        self._stack.addWidget(self._running_pane)
        outer.addWidget(self._stack, 1)

        self._list_pane.run_requested.connect(self._on_run_clicked)
        self._list_pane.back_requested.connect(self.back_requested)
        self._running_pane.done_clicked.connect(self._on_running_done)

    def show_for(self, cfg: Config) -> None:
        self._cfg = cfg
        entries = _list_entries(Path(cfg.state_dir))
        self._list_pane.populate(entries, cfg.library_slug)
        self._stack.setCurrentWidget(self._list_pane)

    @Slot(object)
    def _on_run_clicked(self, entry: _UndoEntry) -> None:
        answer = QMessageBox.question(
            self,
            "Run undo?",
            f"This will reverse the file moves from {entry.display_time}.\n\n"
            "Files currently at their new locations will be moved back to "
            "where they were before that run. This is not itself reversible.\n\n"
            "Proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._running_pane.start(entry)
        self._stack.setCurrentWidget(self._running_pane)
        self._list_pane.set_running(entry)

        self._worker = UndoWorker(entry.path, parent=self)
        self._worker.progress.connect(self._running_pane.status.push)
        self._worker.finished_with_result.connect(self._on_undo_done)
        self._worker.failed.connect(self._on_undo_failed)
        self._worker.start()

    @Slot(object)
    def _on_undo_done(self, rc: int) -> None:
        self._running_pane.finish(int(rc))
        self._list_pane.set_running(None)

    @Slot(str)
    def _on_undo_failed(self, message: str) -> None:
        self._running_pane.set_failure_message(message)
        self._list_pane.set_running(None)

    @Slot()
    def _on_running_done(self) -> None:
        if self._cfg is not None:
            self.show_for(self._cfg)
        self._stack.setCurrentWidget(self._list_pane)
