"""DedupeWorker — reads 01_tags.csv, groups duplicates, writes 07_*.csv.

The library's ``group_duplicates`` is synchronous and has no progress
callback (it's fast — in-memory grouping of CSV rows). The worker
brackets it with synthetic ``ProgressEvent``s so the UI shows
movement: "reading…", "grouping…", "writing…".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from musicorg import (
    Config,
    ProgressEvent,
    group_duplicates,
    read_tags_csv,
    write_dedupe_outputs,
)

from .base import LibraryWorker


@dataclass
class DedupeOutcome:
    """Summary returned by DedupeWorker.finished_with_result."""

    winners: list[dict]
    losers: list[dict]
    groups: list[dict]
    outputs: dict[str, Path]


class DedupeWorker(LibraryWorker):
    """Group near-duplicate audio files and pick a winner per group."""

    def __init__(self, cfg: Config, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg = cfg

    def do_work(self, progress_cb: Callable[[ProgressEvent], None]) -> DedupeOutcome:
        state_dir = Path(self._cfg.state_dir)
        tags_csv = state_dir / "01_tags.csv"

        progress_cb(ProgressEvent(phase="dedupe", current=0, total=3, path=str(tags_csv), message="Reading 01_tags.csv"))
        rows = read_tags_csv(tags_csv)

        progress_cb(ProgressEvent(phase="dedupe", current=1, total=3, path="", message=f"Grouping {len(rows)} rows…"))
        winners, losers, groups = group_duplicates(rows)

        progress_cb(ProgressEvent(phase="dedupe", current=2, total=3, path=str(state_dir), message="Writing 07_winners.csv, 07_duplicates.csv, 07_groups.csv"))
        outputs = write_dedupe_outputs(winners, losers, groups, state_dir, source_rows=rows)

        progress_cb(ProgressEvent(phase="dedupe", current=3, total=3, path="", message=f"Done — {len(groups)} groups, {len(winners)} winners, {len(losers)} losers"))
        return DedupeOutcome(winners=winners, losers=losers, groups=groups, outputs=outputs)
