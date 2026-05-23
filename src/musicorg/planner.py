"""Translate resolved metadata into target paths under ``<library>/Music/``.

Implements the demote-to-single rule (album_track → single when fewer
than 2 or 3 siblings share an album, depending on year availability) and
the Punjabi-vs-Bollywood routing for singles. The PUNJABI_HINTS list
lives here because it is plan-tree-specific routing, not a generic
cleanup concern.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

from .clean import normalize_album, safe, strip_year_suffix


PUNJABI_HINTS: tuple[str, ...] = (
    "honey singh", "diljit", "jazzy b", "j-star", "badshah", "yo yo",
    "dr zeus", "d.r.zeus", "hardy sandhu", "raftaar", "gippy", "sukhbir",
    "guru randhawa", "sidhu moose wala", "ammy virk", "ranjit bawa",
    "garry sandhu", "bohemia", "manj musik", "mickey singh", "jaz dhami",
)


PLAN_FIELDS: list[str] = [
    "source", "destination", "country", "album_or_movie", "year",
    "artist", "title", "track_num", "category", "bitrate_kbps",
    "needs_lookup", "action",
]


def decade_of(year: str) -> str:
    """Return ``2010s``, ``1990s``, ... or ``Unknown Year``."""
    if not year or len(year) < 4 or not year[:4].isdigit():
        return "Unknown Year"
    y = int(year[:4])
    return f"{(y // 10) * 10}s"


def build_destination(row: dict, music_root: Path) -> str:
    """Compute the target absolute path for one resolved row.

    ``music_root`` is the library root that contains the ``Music/`` tree
    (the caller is expected to pass ``cfg.root_path``). The function
    appends ``Music/<bucket>/...`` to mirror the source pipeline layout.
    """
    root = Path(music_root) / "Music"
    country = row.get("country", "")
    year = row.get("year", "")
    album = normalize_album(strip_year_suffix(safe(row.get("album_or_movie", ""))))
    title = safe(row.get("title", "")) or Path(row.get("filename", "")).stem
    track = row.get("track_num", "")
    ext = Path(row.get("filename", "")).suffix.lower()

    if title and track and track.isdigit():
        m = re.match(r"^\s*(\d{1,3})[\s.\-]+(.+)$", title)
        if m and int(m.group(1)) == int(track):
            title = m.group(2).strip()

    if track and track.isdigit() and 1 <= int(track) <= 99:
        leaf = f"{int(track):02d} - {title}{ext}"
    else:
        leaf = f"{title}{ext}"
    leaf = safe(leaf, maxlen=150)

    decade = decade_of(year)
    year_suffix = f" ({year})" if year else ""

    if country == "hollywood":
        artist = safe(row.get("artist", "")) or "Unknown Artist"
        if row.get("category") == "album_track" and album:
            return str(root / "Hollywood" / decade / artist / f"{album}{year_suffix}" / leaf)
        return str(root / "Singles" / "Hollywood" / artist / leaf)

    if country == "bollywood":
        if row.get("category") == "single" or not album or not year:
            artist = safe(row.get("artist", "")) or "Unknown Artist"
            lang = "Punjabi" if any(w in artist.lower() for w in PUNJABI_HINTS) else "Bollywood"
            return str(root / "Singles" / lang / artist / leaf)
        return str(root / "Bollywood" / decade / f"{album}{year_suffix}" / leaf)

    return str(root / "Unsorted" / leaf)


def _album_counts(rows: list[dict]) -> Counter:
    counts: Counter = Counter()
    for r in rows:
        key = (
            normalize_album(strip_year_suffix(safe(r.get("album_or_movie", "")))).lower(),
            r.get("year", ""),
        )
        if key[0]:
            counts[key] += 1
    return counts


def plan(resolved_csv_path: Path, out_path: Path, music_root: Path) -> dict:
    """Read ``08_resolved.csv``, apply demotion rules, write ``09_plan.csv``.

    Returns ``{"total", "by_country", "by_category", "path"}``.
    """
    resolved_csv_path = Path(resolved_csv_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with resolved_csv_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    album_counts = _album_counts(rows)

    plan_rows: list[dict] = []
    for r in rows:
        row = dict(r)
        row["album_or_movie"] = re.sub(
            r"\s*\(Unknown\b[^)]*\)\s*", "", row.get("album_or_movie", "")
        ).strip()
        if row["album_or_movie"].lower() in ("unknown album", "<unknown>", ""):
            row["album_or_movie"] = ""

        if row.get("category") == "album_track":
            parent = row.get("current_folder", "")
            parent_album_shape = bool(re.match(r"^.*\(\d{4}\)\s*$", parent or ""))
            if not parent_album_shape and row["album_or_movie"]:
                key = (
                    normalize_album(strip_year_suffix(safe(row["album_or_movie"]))).lower(),
                    row.get("year", ""),
                )
                threshold = 2 if row.get("year") else 3
                if album_counts.get(key, 0) < threshold:
                    row["category"] = "single"
                    row["album_or_movie"] = ""

        dest = build_destination(row, music_root)
        plan_rows.append({
            "source": row.get("path", ""),
            "destination": dest,
            "country": row.get("country", ""),
            "album_or_movie": row["album_or_movie"],
            "year": row.get("year", ""),
            "artist": row.get("artist", ""),
            "title": row.get("title", ""),
            "track_num": row.get("track_num", ""),
            "category": row.get("category", ""),
            "bitrate_kbps": row.get("bitrate_kbps", ""),
            "needs_lookup": row.get("needs_lookup", ""),
            "action": "move" if row.get("path", "") != dest else "noop",
        })

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PLAN_FIELDS)
        w.writeheader()
        w.writerows(plan_rows)

    by_country = Counter(r["country"] for r in plan_rows)
    by_category = Counter(r["category"] for r in plan_rows)
    return {
        "total": len(plan_rows),
        "by_country": dict(by_country),
        "by_category": dict(by_category),
        "path": out_path,
    }
