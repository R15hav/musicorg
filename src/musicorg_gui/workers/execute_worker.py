"""ExecuteWorker — applies the planned moves/copies/symlinks with undo."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal

from musicorg import Config, ProgressEvent, execute_plan

from .base import LibraryWorker


ApplyMode = Literal["move", "copy", "symlink"]


class ExecuteWorker(LibraryWorker):
    """Move / copy / symlink files into the planned tree.

    The library's ``execute_plan`` writes an ``undo_<TS>.sh`` next to the
    state directory; the worker just surfaces the returned stats dict.
    """

    def __init__(
        self,
        cfg: Config,
        mode: ApplyMode,
        *,
        dry_run: bool = False,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._mode = mode
        self._dry_run = dry_run

    def do_work(self, progress_cb: Callable[[ProgressEvent], None]) -> dict:
        state = Path(self._cfg.state_dir)
        stats = execute_plan(
            state / "09_plan.csv",
            state / "07_duplicates.csv",
            state,
            mode=self._mode,
            dry_run=self._dry_run,
            progress=progress_cb,
        )
        return stats
