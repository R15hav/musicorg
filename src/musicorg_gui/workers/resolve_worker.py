"""ResolveWorker — reconciles 07_winners.csv into 08_resolved.csv."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from musicorg import Config, ProgressEvent, resolve_winners

from .base import LibraryWorker


class ResolveWorker(LibraryWorker):
    """Reconcile folder, tag, and filename evidence into a canonical record."""

    def __init__(self, cfg: Config, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg = cfg

    def do_work(self, progress_cb: Callable[[ProgressEvent], None]) -> dict:
        state = Path(self._cfg.state_dir)
        progress_cb(ProgressEvent(
            phase="resolve", current=0, total=1, path="",
            message="Reconciling folder, tag, and filename evidence…",
        ))
        stats = resolve_winners(
            state / "07_winners.csv",
            state / "08_resolved.csv",
            default_country=self._cfg.default_country,
        )
        progress_cb(ProgressEvent(
            phase="resolve", current=1, total=1, path="",
            message=f"Resolved {stats.get('total', 0)} rows",
        ))
        return stats
