"""Reconcile per-winner metadata into a canonical (artist, album, year, title) tuple.

Folder, tag, and filename evidence are combined here. Country detection
(Bollywood vs Hollywood) and category classification (album_track vs
single) live in this module because both are resolve-specific allowlists
that don't belong in the shared :mod:`musicorg.clean` regex tables.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from .clean import (
    FOLDER_RX,
    normalize_year,
    parse_folder,
    parse_track_num,
    strip_junk,
    title_from_filename,
)


HOLLYWOOD_ARTISTS: set[str] = {
    "linkin park", "jennifer lopez", "j-lo", "coldplay", "eminem",
    "rihanna", "beyonce", "adele", "ed sheeran", "katy perry", "lady gaga",
    "maroon 5", "imagine dragons", "the weeknd", "drake", "bruno mars",
    "taylor swift", "shakira", "pitbull", "akon", "one direction",
    "cigarettes after sex", "chezile", "calvin harris", "sam smith",
    "justin bieber", "selena gomez", "ariana grande", "billie eilish",
    "post malone", "twenty one pilots", "panic! at the disco", "shawn mendes",
    "harry styles", "olivia rodrigo", "halsey",
}

HOLLYWOOD_ALBUMS: set[str] = {
    "living things", "thousand suns", "minutes to midnight", "meteora",
    "hybrid theory", "one more light", "the hunting party",
}


RESOLVED_FIELDS: list[str] = [
    "path", "filename", "current_folder", "country", "album_or_movie",
    "year", "artist", "title", "track_num", "category", "confidence",
    "needs_lookup", "tag_source", "notes", "bitrate_kbps",
]


def detect_country(artist: str, album: str, default: str = "bollywood") -> str:
    """Heuristic country routing. Unknown stays as ``default``."""
    a = (artist or "").lower()
    al = (album or "").lower()
    for h in HOLLYWOOD_ARTISTS:
        if h in a:
            return "hollywood"
    for h in HOLLYWOOD_ALBUMS:
        if h in al:
            return "hollywood"
    return default


def category_from_folder(folder_name: str, album: str, year: str) -> str:
    """Classify a file as album_track / single / unknown using the folder shape."""
    if FOLDER_RX.match(folder_name or ""):
        return "album_track"
    if album and year and len(year) == 4 and year.isdigit():
        if "single" not in album.lower():
            return "album_track"
    fl = (folder_name or "").lower()
    if "single" in fl or "feat." in fl:
        return "single"
    if "Unknown" in (folder_name or "") or "(Unknown Year)" in (folder_name or ""):
        return "single"
    return "unknown"


def reconcile(row: dict, default_country: str = "bollywood") -> dict:
    """Return the canonical resolved record for one winner CSV row."""
    folder = row.get("parent_folder", "") or ""
    folder_album, folder_year = parse_folder(folder)
    tag_artist = strip_junk(row.get("artist", ""))
    tag_album = strip_junk(row.get("album", ""))
    tag_title = strip_junk(row.get("title", ""))
    tag_year = normalize_year(row.get("year", ""))
    file_title = title_from_filename(row.get("filename", ""))

    if folder_album and folder_year:
        album = folder_album
    else:
        album = tag_album

    year = folder_year or tag_year
    title = tag_title or file_title
    artist = tag_artist
    track = parse_track_num(row.get("filename", ""), row.get("track", ""))
    country = detect_country(artist, album, default=default_country)
    category = category_from_folder(folder, album, year)

    missing: list[str] = []
    if not year:
        missing.append("year")
    if not album and category == "album_track":
        missing.append("album")
    if not title:
        missing.append("title")
    if not artist:
        missing.append("artist")
    needs_lookup = "+".join(missing)
    confidence = "high" if not missing else ("medium" if len(missing) <= 2 else "low")

    return {
        "path": row.get("path", ""),
        "filename": row.get("filename", ""),
        "current_folder": folder,
        "country": country,
        "album_or_movie": album,
        "year": year,
        "artist": artist,
        "title": title,
        "track_num": track,
        "category": category,
        "confidence": confidence,
        "needs_lookup": needs_lookup,
        "tag_source": row.get("tag_source", ""),
        "notes": "",
        "bitrate_kbps": row.get("bitrate_kbps", ""),
    }


def resolve_winners(
    winners_csv_path: Path,
    out_path: Path,
    default_country: str = "bollywood",
) -> dict:
    """Reconcile every row of a winners CSV and emit ``08_resolved.csv``.

    Returns ``{"total", "confidence": Counter, "country": Counter, "path"}``.
    """
    winners_csv_path = Path(winners_csv_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conf_counter: Counter = Counter()
    country_counter: Counter = Counter()
    total = 0
    with winners_csv_path.open("r", encoding="utf-8", newline="") as fin, \
         out_path.open("w", encoding="utf-8", newline="") as fout:
        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=RESOLVED_FIELDS)
        writer.writeheader()
        for row in reader:
            res = reconcile(row, default_country=default_country)
            conf_counter[res["confidence"]] += 1
            country_counter[res["country"]] += 1
            writer.writerow(res)
            total += 1

    return {
        "total": total,
        "confidence": dict(conf_counter),
        "country": dict(country_counter),
        "path": out_path,
    }
