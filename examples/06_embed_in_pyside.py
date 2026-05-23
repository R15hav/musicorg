"""Example 06 — embed musicorg in a PySide6 desktop GUI.

ILLUSTRATIVE SKELETON — not runnable without PySide6 installed.
Requires:  pip install pyside6

Key integration challenge: musicorg.scan() blocks for potentially many
seconds or minutes.  Qt's UI is single-threaded, so the scan must run on a
QThread (or Python threading.Thread).  ProgressEvent callbacks arrive on
that worker thread.  Qt requires that all widget updates happen on the main
thread.  The bridge is a Qt signal: the worker emits progress_updated, Qt
delivers it on the main thread, and the slot updates the progress bar safely.

Design shown here:
  - ScanWorker(QThread) wraps scan() and emits two signals: progress_updated
    (one ProgressEvent per file) and scan_finished (list[Track] on completion).
  - MainWindow connects those signals to slots that update QProgressBar and
    QListWidget on the main thread.
  - "Choose folder" button opens QFileDialog, kicks off the worker.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Attempt PySide6 import; degrade gracefully for syntax-check purposes.
# ---------------------------------------------------------------------------
try:
    from PySide6.QtCore import QThread, Signal, Slot
    from PySide6.QtWidgets import (
        QApplication,
        QFileDialog,
        QLabel,
        QListWidget,
        QMainWindow,
        QProgressBar,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
    _PYSIDE_AVAILABLE = True
except ImportError:
    _PYSIDE_AVAILABLE = False
    # Minimal stubs so the rest of the file is syntactically valid.
    class QThread:  # type: ignore[no-redef]
        def start(self) -> None: ...
        def run(self) -> None: ...
    class Signal:  # type: ignore[no-redef]
        def __init__(self, *types: Any) -> None: ...
        def emit(self, *args: Any) -> None: ...
        def connect(self, slot: Any) -> None: ...
    def Slot(*types: Any) -> Any:  # type: ignore[no-redef]
        def decorator(fn: Any) -> Any:
            return fn
        return decorator
    class QApplication:  # type: ignore[no-redef]
        def __init__(self, argv: Any) -> None: ...
        def exec(self) -> int: return 0
    class QFileDialog:  # type: ignore[no-redef]
        @staticmethod
        def getExistingDirectory(*a: Any, **kw: Any) -> str: return ""
    class _QWidget:
        def __init__(self, *a: Any) -> None: ...
        def setWindowTitle(self, t: str) -> None: ...
        def setCentralWidget(self, w: Any) -> None: ...
        def show(self) -> None: ...
        def addItem(self, s: str) -> None: ...
        def setMaximum(self, n: int) -> None: ...
        def setValue(self, n: int) -> None: ...
        def setText(self, s: str) -> None: ...
    QMainWindow = QWidget = QLabel = QListWidget = QProgressBar = QPushButton = QVBoxLayout = _QWidget  # type: ignore[misc,assignment]

from musicorg import load_config, scan, ProgressEvent, Track


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class ScanWorker(QThread):
    """Runs musicorg.scan() on a background thread and emits progress signals.

    Signals are the Qt-idiomatic bridge between threads.  Never touch widgets
    directly from this class — only emit signals and let the connected slots
    (which run on the main thread) do the UI work.
    """

    # Signal payloads: (phase, current, total, path, error_flag)
    progress_updated = Signal(str, int, int, str, bool)
    # Emitted once when scan completes; carries the full track list.
    scan_finished = Signal(list)

    def __init__(self, music_root: str) -> None:
        super().__init__()
        self.music_root = music_root

    def run(self) -> None:
        """Called by Qt in the worker thread when start() is invoked."""
        cfg = load_config(state_root=Path("/tmp/musicorg-pyside-example"))

        def on_progress(ev: ProgressEvent) -> None:
            # emit() is thread-safe in Qt — the signal is queued to the main
            # thread's event loop, so the connected slot executes there.
            self.progress_updated.emit(ev.phase, ev.current, ev.total, ev.path, ev.error)

        tracks = scan(cfg, root=Path(self.music_root), progress=on_progress)
        self.scan_finished.emit(tracks)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("musicorg example")
        self._worker: ScanWorker | None = None

        # Build the widget hierarchy.
        central = QWidget()
        layout = QVBoxLayout()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self._status_label = QLabel("Choose a folder to scan.")
        layout.addWidget(self._status_label)

        self._choose_btn = QPushButton("Choose folder...")
        self._choose_btn.clicked.connect(self._on_choose_folder)
        layout.addWidget(self._choose_btn)

        self._progress_bar = QProgressBar()
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)

        self._results_list = QListWidget()
        layout.addWidget(self._results_list)

    @Slot()
    def _on_choose_folder(self) -> None:
        """Open a folder dialog and kick off a scan worker thread."""
        folder = QFileDialog.getExistingDirectory(self, "Select music folder")
        if not folder:
            return

        self._results_list.clear()
        self._progress_bar.setValue(0)
        self._status_label.setText(f"Scanning {folder} ...")

        # Create a new worker and wire up its signals before calling start().
        self._worker = ScanWorker(folder)
        self._worker.progress_updated.connect(self._on_progress)
        self._worker.scan_finished.connect(self._on_finished)
        self._worker.start()

    @Slot(str, int, int, str, bool)
    def _on_progress(self, phase: str, current: int, total: int, path: str, error: bool) -> None:
        """Slot runs on the main thread — safe to update widgets here."""
        if total > 0:
            self._progress_bar.setMaximum(total)
            self._progress_bar.setValue(current)
        tail = path[-60:] if len(path) > 60 else path
        self._status_label.setText(f"{phase}  {current}/{total}  {tail}")

    @Slot(list)
    def _on_finished(self, tracks: list[Track]) -> None:
        """Slot runs on the main thread — display results in the list widget."""
        self._status_label.setText(f"Done — {len(tracks)} files scanned.")
        for t in tracks:
            fp = t.fingerprint_sha256[:12] if t.fingerprint_sha256 else "------------"
            self._results_list.addItem(f"{fp}  {t.title or '<no title>'}  —  {t.artist or '<no artist>'}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not _PYSIDE_AVAILABLE:
        print("PySide6 is not installed.  Run: pip install pyside6")
        sys.exit(1)
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
