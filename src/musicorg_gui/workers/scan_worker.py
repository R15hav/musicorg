"""ScanWorker — wraps ``musicorg.scan`` in a QThread.

First concrete worker; serves as the reference implementation other
phase workers (dedupe, resolve, plan, execute, canonicalize, upgrade)
will follow.

On a re-run of the same library, the post-execute organize tree at
``<root>/Music/`` would otherwise get walked recursively and produce
ghost duplicates of every track. We exclude that subtree explicitly so
re-runs only see the originals.
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
        # Don't re-scan the post-execute output tree on subsequent runs.
        # ``<root>/Music/`` is the canonical Stage-1 destination; once
        # execute has run, that subtree mirrors the same content the
        # original files carry. Walking it would double every track.
        organized_root = (self._root / "Music").resolve()
        extra_excludes = [str(organized_root)] if organized_root.exists() else None

        tracks = scan(
            self._cfg,
            root=self._root,
            extra_excludes=extra_excludes,
            progress=progress_cb,
        )
        write_tags_csv(tracks, Path(self._cfg.state_dir) / "01_tags.csv")
        return tracks
