"""Resolved paths to external binaries: ffprobe, mediainfo, ffmpeg.

The library shells out to these three tools for tag reading, lossless
verification, and audio-stream fingerprinting. By default, bare command
names are used so subprocess relies on ``PATH``. Two override mechanisms
exist:

1. **Environment variables** ``MUSICORG_FFPROBE``, ``MUSICORG_MEDIAINFO``,
   ``MUSICORG_FFMPEG``. Read at import time. Useful for CI, system
   installs, and per-process overrides.

2. **Runtime setter** :func:`set_binary_paths`. Overrides the resolved
   paths for the rest of the process lifetime. Embedders that bundle or
   download binaries to a known cache directory (the desktop GUI does
   this — see [[project-gui-scope]]) call this once at startup before
   any library function runs.

The setter is process-global by design: there is no use case for
swapping binaries mid-pipeline, and threading the value through every
public function would force a breaking signature change on every
embedder. Embedders that genuinely need per-call overrides can wrap
calls with the env vars instead.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["set_binary_paths", "ffprobe_path", "mediainfo_path", "ffmpeg_path"]


_ffprobe: str = os.environ.get("MUSICORG_FFPROBE") or "ffprobe"
_mediainfo: str = os.environ.get("MUSICORG_MEDIAINFO") or "mediainfo"
_ffmpeg: str = os.environ.get("MUSICORG_FFMPEG") or "ffmpeg"


def set_binary_paths(
    *,
    ffprobe: str | Path | None = None,
    mediainfo: str | Path | None = None,
    ffmpeg: str | Path | None = None,
) -> None:
    """Override the binary paths used by the library.

    Each argument may be a bare command name (relies on ``PATH``) or an
    absolute path to an executable. Passing ``None`` leaves a binary
    unchanged from its current value.

    Process-global. Call once at startup before any library function.
    Safe to call multiple times — the last call wins.
    """
    global _ffprobe, _mediainfo, _ffmpeg
    if ffprobe is not None:
        _ffprobe = str(ffprobe)
    if mediainfo is not None:
        _mediainfo = str(mediainfo)
    if ffmpeg is not None:
        _ffmpeg = str(ffmpeg)


def ffprobe_path() -> str:
    """Return the currently resolved ffprobe command or path."""
    return _ffprobe


def mediainfo_path() -> str:
    """Return the currently resolved mediainfo command or path."""
    return _mediainfo


def ffmpeg_path() -> str:
    """Return the currently resolved ffmpeg command or path."""
    return _ffmpeg
