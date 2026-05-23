"""Full-tree audio scanner.

Walks a library root, applies the configured exclude prefixes plus the
default skip-list (``_duplicates``, ``_misc``, ``_organizer``, ...), and
reads tags via :mod:`musicorg.tags`. Output is a flat list of ``Track``
records which downstream phases (dedupe, resolve, plan) consume.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import tags as tags_mod
from .config import Config, DEFAULT_EXCLUDE_DIR_NAMES, is_path_excluded
from .models import Track


SCAN_CSV_COLUMNS: list[str] = [
    "path", "filename", "parent_folder", "size_bytes",
    "bitrate_kbps", "duration_sec", "tag_source",
    "title", "artist", "album", "albumartist",
    "year", "track", "genre",
]


@dataclass
class ScanStats:
    total: int = 0
    mutagen_ok: int = 0
    ffprobe_ok: int = 0
    mediainfo_ok: int = 0
    none: int = 0


def _iter_audio_files(
    root: Path,
    exclude_prefixes: list[str],
) -> Iterable[Path]:
    root = Path(root)
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in tags_mod.AUDIO_EXTS:
            continue
        if is_path_excluded(p, exclude_prefixes):
            continue
        if any(part in DEFAULT_EXCLUDE_DIR_NAMES for part in p.parts):
            continue
        yield p


def scan(
    root: Path,
    cfg: Config,
    extra_excludes: list[Path] | None = None,
    progress: Callable[[int, int, Path], None] | None = None,
) -> list[Track]:
    """Walk ``root`` recursively and return one ``Track`` per audio file.

    Skips paths under ``cfg.exclude_prefixes``, ``extra_excludes``, and any
    component listed in :data:`DEFAULT_EXCLUDE_DIR_NAMES`. ``progress`` is
    invoked as ``progress(index, total, path)`` per file when supplied.
    """
    excludes: list[str] = list(cfg.exclude_prefixes or [])
    if extra_excludes:
        excludes.extend(str(Path(p)) for p in extra_excludes)
    files = sorted(_iter_audio_files(Path(root), excludes))
    total = len(files)
    tracks: list[Track] = []
    for i, p in enumerate(files, 1):
        t = tags_mod.read(p)
        tracks.append(t)
        if progress is not None:
            progress(i, total, p)
    return tracks


def _bucket_source(src: str) -> str:
    if src.startswith("mutagen-ok"):
        return "mutagen_ok"
    if src.startswith("ffprobe-ok"):
        return "ffprobe_ok"
    if src.startswith("mediainfo-ok"):
        return "mediainfo_ok"
    return "none"


def compute_stats(tracks: list[Track]) -> ScanStats:
    """Bucket the per-file ``tag_source`` codes into coverage counters."""
    stats = ScanStats(total=len(tracks))
    for t in tracks:
        has_any = bool(t.title or t.artist or t.album)
        if not has_any:
            stats.none += 1
            continue
        bucket = _bucket_source(t.tag_source)
        if bucket == "mutagen_ok":
            stats.mutagen_ok += 1
        elif bucket == "ffprobe_ok":
            stats.ffprobe_ok += 1
        elif bucket == "mediainfo_ok":
            stats.mediainfo_ok += 1
        else:
            stats.none += 1
    return stats


def write_tags_csv(tracks: list[Track], out_path: Path) -> ScanStats:
    """Persist ``tracks`` to a CSV row-per-file. Returns the coverage stats."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SCAN_CSV_COLUMNS)
        w.writeheader()
        for t in tracks:
            w.writerow({
                "path": str(t.path),
                "filename": t.path.name,
                "parent_folder": t.path.parent.name,
                "size_bytes": t.size,
                "bitrate_kbps": t.bitrate_kbps or 0,
                "duration_sec": round(t.duration_sec or 0.0, 1),
                "tag_source": t.tag_source,
                "title": t.title,
                "artist": t.artist,
                "album": t.album,
                "albumartist": t.albumartist,
                "year": t.year,
                "track": t.track,
                "genre": t.genre,
            })
    return compute_stats(tracks)
