"""UndoWorker — runs a generated undo_<TS>.sh on a background thread.

The library's ``run_undo`` is a blocking subprocess call; isolating it
on a QThread keeps the UI responsive even on large libraries where the
undo script may move hundreds of files back.

The shell script itself decides what to do — the worker just spawns it
and returns the shell exit code. Anything more sophisticated (preview
which files would move back, partial undo) would require parsing the
generated script, which we deliberately don't do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from musicorg import ProgressEvent, run_undo

from .base import LibraryWorker


class UndoWorker(LibraryWorker):
    """Execute one undo_<TS>.sh and report the exit code."""

    def __init__(self, script_path: Path, parent: Any = None) -> None:
        super().__init__(parent)
        self._script_path = script_path

    def do_work(self, progress_cb: Callable[[ProgressEvent], None]) -> int:
        progress_cb(ProgressEvent(
            phase="undo", current=0, total=1, path=str(self._script_path),
            message=f"Running {self._script_path.name}…",
        ))
        rc = run_undo(self._script_path)
        progress_cb(ProgressEvent(
            phase="undo", current=1, total=1, path=str(self._script_path),
            message=f"Undo finished with exit code {rc}",
            error=(rc != 0),
        ))
        return rc
