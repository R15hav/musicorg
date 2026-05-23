"""UpgradeWorker — wraps :func:`musicorg.upgrade_batch`.

Builds the candidate list off-thread by reading
``<state_dir>/30_shazam_refingerprint.csv`` if present, falling back to
``<state_dir>/16_merged.csv`` (matching the CLI's ``upgrade`` command at
``src/musicorg_cli/main.py:527-543``). Each row with a non-empty
``apple_music_url`` (or ``api_track_view_url``) becomes a candidate.

The worker pulls the gamdl cookies / Widevine device paths from the
``Config`` it was created with. Empty strings are treated as "not
configured" and converted to ``None`` before the library call.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Callable

from musicorg import Config, ProgressEvent, upgrade_batch

from .base import LibraryWorker


class UpgradeWorker(LibraryWorker):
    """Run ``upgrade_batch`` over candidates harvested from the state CSVs."""

    def __init__(self, cfg: Config, *, dry_run: bool = False, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._dry_run = dry_run

    @staticmethod
    def _build_candidates(state_dir: Path) -> list[dict]:
        """Read the first existing CSV in (refingerprint, merged) order."""
        candidates: list[dict] = []
        refp = state_dir / "30_shazam_refingerprint.csv"
        merged = state_dir / "16_merged.csv"
        for csv_path in (refp, merged):
            if not csv_path.exists():
                continue
            with csv_path.open(newline="") as fh:
                for row in csv.DictReader(fh):
                    url = (
                        (row.get("apple_music_url") or "").strip()
                        or (row.get("api_track_view_url") or "").strip()
                    )
                    if not url:
                        continue
                    candidates.append({
                        "path": row.get("source_path") or row.get("path") or "",
                        "apple_music_url": url,
                        "adam_id": (row.get("adam_id") or "").strip(),
                        "storefront": (row.get("storefront") or "").strip(),
                    })
            # Prefer the refingerprint CSV when both exist — match CLI behavior.
            break
        return candidates

    def do_work(self, progress_cb: Callable[[ProgressEvent], None]) -> dict:
        state_dir = Path(self._cfg.state_dir)
        music_root = Path(self._cfg.library_root) if self._cfg.library_root else Path.cwd()

        candidates = self._build_candidates(state_dir)
        progress_cb(ProgressEvent(
            phase="upgrade",
            current=0,
            total=len(candidates),
            path="",
            message=f"{len(candidates)} candidate{'s' if len(candidates) != 1 else ''} to upgrade",
        ))

        cookies = self._cfg.gamdl_cookies_path.strip()
        wvd = self._cfg.gamdl_wvd_path.strip()
        cookies_path = Path(cookies) if cookies else None
        wvd_path = Path(wvd) if wvd else None

        summary = upgrade_batch(
            candidates=candidates,
            music_root=music_root,
            state_dir=state_dir,
            dry_run=self._dry_run,
            cookies_path=cookies_path,
            wvd_path=wvd_path,
            progress=progress_cb,
        )
        return summary
