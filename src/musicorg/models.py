"""Dataclasses used across the musicorg pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


Country = Literal["bollywood", "hollywood", "unknown"]
Decision = Literal["auto_apply", "review", "low", "no_match"]
Category = Literal["album_track", "single", "unknown"]
ApplyMode = Literal["move", "copy", "symlink"]


@dataclass
class Track:
    path: Path
    size: int = 0
    bitrate_kbps: int = 0
    duration_sec: float = 0.0
    tag_source: str = "none"
    title: str = ""
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    year: str = ""
    track: str = ""
    genre: str = ""


@dataclass
class ResolvedTrack:
    path: Path
    filename: str = ""
    current_folder: str = ""
    country: Country = "unknown"
    album_or_movie: str = ""
    year: str = ""
    artist: str = ""
    title: str = ""
    track_num: str = ""
    category: Category = "unknown"
    confidence: str = "low"
    needs_lookup: str = ""
    tag_source: str = ""
    notes: str = ""
    bitrate_kbps: int = 0


@dataclass
class TierMatch:
    tier: Literal["itunes", "jiosaavn", "shazam"]
    confidence: float = 0.0
    title: str = ""
    artist: str = ""
    album: str = ""
    year: str = ""
    track_num: str = ""
    genre: str = ""
    cover_url: str = ""
    apple_music_url: str = ""
    adam_id: str = ""
    storefront: str = ""
    isrc: str = ""
    raw: dict = field(default_factory=dict)
    score_parts: dict = field(default_factory=dict)


@dataclass
class ApplyResult:
    files_touched: int = 0
    tags_written: int = 0
    renamed: int = 0
    guarded: int = 0
    collisions: int = 0
    errors: int = 0
    snapshot_path: Path | None = None
    undo_script_path: Path | None = None
