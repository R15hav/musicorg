"""Workers for Stage 2 — metadata canonicalization + apply.

Two workers, used back-to-back by the MetadataScreen:

- :class:`CanonicalizeWorker` runs the tiered lookup loop
  (:func:`musicorg.canonicalize_library`) over the organized music tree
  and writes ``16_merged.csv``.
- :class:`ApplyApprovalsWorker` consumes a generated
  ``19_approvals.json`` and rewrites tags via
  :func:`musicorg.apply_approvals`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from musicorg import (
    ApplyResult,
    Config,
    ProgressEvent,
    apply_approvals,
    canonicalize_library,
)

from .base import LibraryWorker


class CanonicalizeWorker(LibraryWorker):
    """Tiered iTunes → JioSaavn → Shazam lookup over the music tree."""

    def __init__(self, cfg: Config, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg = cfg

    def do_work(self, progress_cb: Callable[[ProgressEvent], None]) -> dict[str, int]:
        return canonicalize_library(self._cfg, progress=progress_cb)


class ApplyApprovalsWorker(LibraryWorker):
    """Apply user approvals from an approvals JSON to the merged CSV."""

    def __init__(
        self,
        cfg: Config,
        approvals_json: Path,
        *,
        dry_run: bool = False,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._approvals_json = approvals_json
        self._dry_run = dry_run

    def do_work(self, progress_cb: Callable[[ProgressEvent], None]) -> ApplyResult:
        state = Path(self._cfg.state_dir)
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_path = state / "backups" / f"tag_snapshot_{ts}.json"
        undo_script_path = state / f"undo_phase18_{ts}.py"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)

        result = apply_approvals(
            state / "16_merged.csv",
            self._approvals_json,
            snapshot_path,
            undo_script_path,
            dry_run=self._dry_run,
            progress=progress_cb,
        )
        return result
