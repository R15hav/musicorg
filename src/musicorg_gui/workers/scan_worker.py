"""ScanWorker — wraps ``musicorg.scan`` in a QThread.

First concrete worker; serves as the reference implementation other
phase workers (dedupe, resolve, plan, execute, canonicalize, upgrade)
will follow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from musicorg import Config, ProgressEvent, Track, scan, write_tags_csv

from .base import LibraryWorker


class ScanWorker(LibraryWorker):
    """Walk a music root and return one ``Track`` per audio file.

    Also persists the result to ``<state_dir>/01_tags.csv`` so downstream
    phases (dedupe, resolve, plan) can pick up where this leaves off.
    """

    def __init__(self, cfg: Config, root: Path, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._root = root

    def do_work(self, progress_cb: Callable[[ProgressEvent], None]) -> list[Track]:
        tracks = scan(self._cfg, root=self._root, progress=progress_cb)
        write_tags_csv(tracks, Path(self._cfg.state_dir) / "01_tags.csv")
        return tracks
