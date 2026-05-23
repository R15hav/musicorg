"""Sweep stray non-audio top-level files into ``<library>/Music/_misc/``.

AlbumArt JPGs, ``Folder.jpg``, ``desktop.ini``, stray MP4s, and text
detritus accumulate in real-world libraries. We never auto-delete — every
file is moved into a mirror tree under ``_misc`` so the user can review
or recover it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import DEFAULT_EXCLUDE_DIR_NAMES
from .tags import AUDIO_EXTS


IMAGE_EXTS: set[str] = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
TEXT_EXTS: set[str] = {".txt", ".nfo", ".log", ".cue", ".m3u", ".m3u8", ".pls", ".lrc", ".sfv", ".md"}
SYSTEM_NAMES: set[str] = {"desktop.ini", "thumbs.db", ".ds_store"}
VIDEO_EXTS: set[str] = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"}


def _classify(p: Path) -> str | None:
    name = p.name.lower()
    ext = p.suffix.lower()
    if name in SYSTEM_NAMES:
        return "system"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in TEXT_EXTS:
        return "text"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def sweep_misc(
    root: Path,
    music_root: Path,
    include_images: bool = False,
    include_text: bool = False,
    dry_run: bool = False,
) -> dict:
    """Move non-audio top-level junk under ``music_root / "Music" / "_misc"``.

    Source subpath relative to ``root`` is preserved. Categories:

    - ``system``: always swept (``desktop.ini``, ``Thumbs.db``, ``.DS_Store``)
    - ``image``: swept only when ``include_images`` is True
    - ``text``: swept only when ``include_text`` is True
    - ``video``: always swept (stray MP4/MKV files in a music tree)

    Returns ``{"system", "image", "text", "video", "skipped", "dry_run"}``.
    """
    root = Path(root)
    music_root = Path(music_root)
    misc_root = music_root / "Music" / "_misc"

    counts: dict[str, int] = {"system": 0, "image": 0, "text": 0, "video": 0, "skipped": 0}

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in DEFAULT_EXCLUDE_DIR_NAMES for part in p.parts):
            continue
        # Skip anything already inside our own _misc sink.
        try:
            p.relative_to(misc_root)
            continue
        except ValueError:
            pass
        if p.suffix.lower() in AUDIO_EXTS:
            continue

        cat = _classify(p)
        if cat is None:
            counts["skipped"] += 1
            continue
        if cat == "image" and not include_images:
            counts["skipped"] += 1
            continue
        if cat == "text" and not include_text:
            counts["skipped"] += 1
            continue

        try:
            rel = p.relative_to(root)
        except ValueError:
            rel = Path(p.name)
        dst = misc_root / rel

        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                base = dst.with_suffix("")
                ext = dst.suffix
                i = 2
                while True:
                    cand = Path(f"{base} ({i}){ext}")
                    if not cand.exists():
                        dst = cand
                        break
                    i += 1
            shutil.move(str(p), str(dst))

        counts[cat] += 1

    counts["dry_run"] = int(dry_run)
    return counts
