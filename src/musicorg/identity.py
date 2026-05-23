"""Content-addressed identity for audio files.

The audio-stream SHA256 survives tag-only rewrites and renames, making it
the load-bearing join key across pipeline phases. Path is metadata; this is identity.
See _organizer/optimization.md §CORE-11.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Literal


_CHUNK = 1 << 20  # 1 MiB — balances syscall overhead with peak RSS for large FLACs.


def file_sha256(path: Path) -> str:
    """SHA256 of the entire file bytes.

    Used inside the fallback identity when ffmpeg cannot demux the audio stream
    (corrupt container, exotic codec). Whole-file hashing breaks under tag
    rewrites, which is why it is fallback-only — never the primary key.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def duration_ms(path: Path) -> int:
    """Container duration in milliseconds via ``ffprobe``.

    Returns 0 on any failure (missing binary, malformed file, non-numeric output)
    so the fallback identity can still be constructed without raising. Callers
    treat the value as opaque salt for the fallback fingerprint, not a measurement.
    """
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, FileNotFoundError):
        return 0
    if proc.returncode != 0:
        return 0
    raw = proc.stdout.decode("utf-8", errors="replace").strip()
    if not raw:
        return 0
    try:
        return int(float(raw) * 1000)
    except ValueError:
        return 0


def audio_stream_sha256(path: Path) -> str:
    """SHA256 of the demuxed audio stream. Survives tag-only rewrites and renames.

    This is the primary join key across pipeline phases. Tag edits, retag-on-disk,
    and renames all change file bytes/path but leave the audio frames untouched,
    so this hash remains stable. On ffmpeg failure, returns a fallback identity
    of the form ``"fallback_<file_sha256>_<duration_ms>"`` so callers always
    receive a deterministic, comparable string.
    """
    try:
        proc = subprocess.Popen(
            [
                "ffmpeg",
                "-i",
                str(path),
                "-map",
                "0:a",
                "-c",
                "copy",
                "-f",
                "data",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, FileNotFoundError):
        return f"fallback_{file_sha256(path)}_{duration_ms(path)}"

    h = hashlib.sha256()
    assert proc.stdout is not None  # Popen with PIPE always populates stdout.
    while chunk := proc.stdout.read(_CHUNK):
        h.update(chunk)
    proc.wait()
    if proc.returncode != 0:
        return f"fallback_{file_sha256(path)}_{duration_ms(path)}"
    return h.hexdigest()


def identity_quality(fingerprint: str) -> Literal["primary", "fallback"]:
    """Classify a fingerprint produced by :func:`audio_stream_sha256`.

    Returns ``"fallback"`` iff the fingerprint starts with ``"fallback_"``;
    otherwise ``"primary"``. Lets downstream code weight join confidence without
    re-parsing the fingerprint or re-running ffmpeg.
    """
    return "fallback" if fingerprint.startswith("fallback_") else "primary"
