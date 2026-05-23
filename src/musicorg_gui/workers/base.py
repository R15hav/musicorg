"""Abstract base for library QThread workers.

Bridges musicorg's push-based ``ProgressEvent`` callback to Qt's signal/
slot model. ``progress`` carries the full ``ProgressEvent``; the connected
slot runs on the main thread and is free to update widgets. ``finished``
fires once with the result (whatever the subclass returned); ``failed``
fires once with an error string if ``do_work`` raises.

Subclasses implement ``do_work(progress_cb)`` — a normal Python callable
that uses the library API and invokes ``progress_cb(event)`` as it goes.
The base class converts that into the signal-driven flow.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from musicorg import ProgressEvent


class LibraryWorker(QThread):
    """Run a musicorg library call on a background thread.

    The library is single-threaded by contract; this QThread isolates each
    long-running call so the Qt event loop stays responsive. ``cancel()``
    sets a flag that ``do_work`` should consult between iterations — the
    library checkpoints between files, so cancel = stop after the current
    one.
    """

    progress = Signal(object)
    finished_with_result = Signal(object)
    failed = Signal(str)

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def do_work(self, progress_cb: Callable[[ProgressEvent], None]) -> Any:
        raise NotImplementedError

    def run(self) -> None:
        try:
            result = self.do_work(self._emit_progress)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished_with_result.emit(result)

    def _emit_progress(self, event: ProgressEvent) -> None:
        self.progress.emit(event)
