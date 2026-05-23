"""ScanWorker — wraps ``musicorg.scan`` in a QThread.

First concrete worker; serves as the reference implementation other
phase workers (dedupe, resolve, plan, execute, canonicalize, upgrade)
will follow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from musicorg import Config, ProgressEvent, Track, scan

from .base import LibraryWorker


class ScanWorker(LibraryWorker):
    """Walk a music root and return one ``Track`` per audio file."""

    def __init__(self, cfg: Config, root: Path, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._root = root

    def do_work(self, progress_cb: Callable[[ProgressEvent], None]) -> list[Track]:
        return scan(self._cfg, root=self._root, progress=progress_cb)
