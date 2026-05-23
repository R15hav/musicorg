"""Welcome screen — choose a music folder and run a scan.

First step of the wizard: pick a folder, watch the translated status
line update from ``ProgressEvent``, see the resulting ``Track`` count,
then proceed to the Dashboard via the Continue button.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from musicorg import Config, ProgressEvent, Track, ensure_state_dir, load_config

from ..platform import state_root
from ..workers import ScanWorker


class WelcomeScreen(QWidget):
    """Pick a music folder, run a scan, hand the loaded library to MainWindow."""

    library_ready = Signal(object)  # carries the Config of the just-scanned library

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._worker: ScanWorker | None = None
        self._cfg: Config | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(32, 32, 32, 32)

        title = QLabel("musicorg")
        title.setStyleSheet("font-size: 28px; font-weight: 600;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Pick a music folder to scan. The status line below translates each "
            "library event into plain English."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: palette(mid);")
        layout.addWidget(subtitle)

        self._choose_btn = QPushButton("Choose music folder…")
        self._choose_btn.clicked.connect(self._on_choose)
        layout.addWidget(self._choose_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._result = QLabel("")
        self._result.setWordWrap(True)
        self._result.setStyleSheet("font-weight: 600;")
        layout.addWidget(self._result)

        layout.addStretch(1)

        # Continue → row, hidden until a scan completes successfully.
        nav_row = QHBoxLayout()
        nav_row.addStretch(1)
        self._continue_btn = QPushButton("Continue →")
        self._continue_btn.setVisible(False)
        self._continue_btn.setMinimumWidth(140)
        self._continue_btn.clicked.connect(self._on_continue)
        nav_row.addWidget(self._continue_btn)
        layout.addLayout(nav_row)

    def reset(self) -> None:
        """Clear state and show the choose-folder UI again."""
        self._cfg = None
        self._choose_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._status.clear()
        self._result.clear()
        self._continue_btn.setVisible(False)

    @Slot()
    def _on_choose(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select music folder")
        if not folder:
            return
        self._start_scan(Path(folder))

    def _start_scan(self, root: Path) -> None:
        self._cfg = load_config(root=root, state_root=state_root())
        ensure_state_dir(self._cfg)
        self._choose_btn.setEnabled(False)
        self._continue_btn.setVisible(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._status.setText(f"Starting scan in {root}…")
        self._result.clear()

        self._worker = ScanWorker(self._cfg, root, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_with_result.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    @Slot(object)
    def _on_progress(self, event: ProgressEvent) -> None:
        if event.total > 0:
            self._progress.setRange(0, event.total)
            self._progress.setValue(event.current)
        tail = Path(event.path).name if event.path else ""
        if event.error:
            self._status.setText(f"Trouble reading {tail} — file unchanged")
        elif event.phase == "scan":
            self._status.setText(f"Reading tags — {event.current}/{event.total} — {tail}")
        else:
            self._status.setText(event.message or f"{event.phase}  {event.current}/{event.total}")

    @Slot(object)
    def _on_finished(self, tracks: list[Track]) -> None:
        self._choose_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._status.setText("Scan complete.")
        primary = sum(1 for t in tracks if t.fingerprint_sha256 and not t.fingerprint_sha256.startswith("fallback_"))
        self._result.setText(
            f"Scanned {len(tracks)} file{'s' if len(tracks) != 1 else ''} — "
            f"{primary} with primary fingerprints."
        )
        if self._cfg is not None:
            self._continue_btn.setVisible(True)
            self._continue_btn.setFocus()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._choose_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._status.setText(f"Scan failed: {message}")

    @Slot()
    def _on_continue(self) -> None:
        if self._cfg is not None:
            self.library_ready.emit(self._cfg)
