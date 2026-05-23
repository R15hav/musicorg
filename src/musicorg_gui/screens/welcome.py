"""Welcome screen — choose a music folder and run a scan.

The MVP's smallest possible end-to-end path: pick a folder, watch the
translated status line update from ``ProgressEvent``, see the resulting
``Track`` count. Proves the Qt + QThread + library wiring before any of
the real Stage 1 → Stage 3 flows are built on top.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QFileDialog,
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
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._worker: ScanWorker | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(32, 32, 32, 32)

        title = QLabel("musicorg")
        title.setStyleSheet("font-size: 28px; font-weight: 600;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Pick a music folder to scan. The status line below translates each "
            "library event into plain English. Switch to log view from the menu "
            "for the raw stream."
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

    @Slot()
    def _on_choose(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select music folder")
        if not folder:
            return
        self._start_scan(Path(folder))

    def _start_scan(self, root: Path) -> None:
        cfg = load_config(root=root, state_root=state_root())
        ensure_state_dir(cfg)
        self._choose_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._status.setText(f"Starting scan in {root}…")
        self._result.clear()

        self._worker = ScanWorker(cfg, root, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_with_result.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    @Slot(object)
    def _on_progress(self, event: ProgressEvent) -> None:
        # Phase-aware translation. This is the seed of the translated
        # status panel — full mapping table will live in widgets/status_panel.
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

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._choose_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._status.setText(f"Scan failed: {message}")
