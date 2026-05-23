"""First-run binary download dialog.

Modal QDialog shown only when ``bootstrap.ensure_binaries`` needs to
fetch something. PATH-resolved or cache-hit runs skip the dialog
entirely and the MainWindow opens straight away — that's the common
case for developers and AppImage users on systems with ffmpeg already
installed.

The actual download runs on a QThread so the dialog can paint a live
progress bar. The dialog is closed by the worker emitting ``finished``;
the user has a Cancel button that interrupts mid-download (the partial
file is discarded — the cache directory only ever sees verified
binaries).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ..bootstrap import BootstrapResult, ensure_binaries


class _BootstrapWorker(QThread):
    """Background thread that drives ``ensure_binaries``."""

    progress = Signal(str, int, int)
    finished_with_result = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            result = ensure_binaries(progress=self._emit)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished_with_result.emit(result)

    def _emit(self, message: str, done: int, total: int) -> None:
        self.progress.emit(message, done, total)


class BootstrapDialog(QDialog):
    """Modal first-run dialog. Resolves binaries, then closes itself."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("musicorg — first-run setup")
        self.setModal(True)
        self.setFixedWidth(480)

        self._result: BootstrapResult | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        self._title = QLabel("Setting up audio tools")
        self._title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(self._title)

        self._detail = QLabel(
            "musicorg needs ffprobe, mediainfo, and ffmpeg to read tags and "
            "verify lossless streams. Checking your system…"
        )
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._detail)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        layout.addWidget(self._bar)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        button_row.addWidget(self._cancel_btn)
        layout.addLayout(button_row)

        self._worker = _BootstrapWorker(self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_with_result.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

    def start_and_run(self) -> BootstrapResult:
        """Show the dialog, run the worker, return the BootstrapResult.

        Returns synchronously when ``exec()`` exits — either because the
        worker finished or the user cancelled. ``self._result`` carries
        the outcome (None on cancel).
        """
        self._worker.start()
        self.exec()
        return self._result or BootstrapResult()

    @Slot(str, int, int)
    def _on_progress(self, message: str, done: int, total: int) -> None:
        self._detail.setText(message)
        if total > 0:
            self._bar.setRange(0, total)
            self._bar.setValue(done)
        else:
            self._bar.setRange(0, 0)

    @Slot(object)
    def _on_finished(self, result: BootstrapResult) -> None:
        self._result = result
        self.accept()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._detail.setText(f"Setup failed: {message}")
        self._bar.setRange(0, 1)
        self._bar.setValue(0)
        self._cancel_btn.setText("Close")

    @Slot()
    def _on_cancel(self) -> None:
        if self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(2000)
        self.reject()

    def closeEvent(self, event: Any) -> None:
        self._on_cancel()
        super().closeEvent(event)
