"""PlanWorker — builds 09_plan.csv from 08_resolved.csv."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from musicorg import Config, ProgressEvent, plan

from .base import LibraryWorker


class PlanWorker(LibraryWorker):
    """Compute destination paths under <library_root>/Music/."""

    def __init__(self, cfg: Config, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg = cfg

    def do_work(self, progress_cb: Callable[[ProgressEvent], None]) -> dict:
        state = Path(self._cfg.state_dir)
        progress_cb(ProgressEvent(
            phase="plan", current=0, total=1, path="",
            message="Building destination tree…",
        ))
        stats = plan(
            state / "08_resolved.csv",
            state / "09_plan.csv",
            music_root=Path(self._cfg.library_root),
        )
        progress_cb(ProgressEvent(
            phase="plan", current=1, total=1, path="",
            message=f"Planned {stats.get('total', 0)} destinations",
        ))
        return stats
