"""Dataclasses used across the musicorg pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal


Country = Literal["bollywood", "hollywood", "unknown"]
Decision = Literal["auto_apply", "review", "low", "no_match"]
Category = Literal["album_track", "single", "unknown"]
ApplyMode = Literal["move", "copy", "symlink"]


@dataclass
class Track:
    """Per-file record produced by :func:`musicorg.scan`.

    ``fingerprint_sha256`` is the content-addressed join key (CORE-11). An
    empty string means the fingerprint has not been computed; downstream code
    should treat it as "skip" rather than joining on it. ``identity_quality``
    reflects whether the fingerprint is primary (audio-stream) or fallback
    (whole-file sha256).
    """

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
    # CORE-11: audio-stream sha256; primary join key across phases (see identity.py).
    # "" means not-yet-computed — downstream code should treat empty as "skip" rather than join on it.
    fingerprint_sha256: str = ""
    identity_quality: Literal["primary", "fallback", "none"] = "none"


@dataclass
class ProgressEvent:
    """Phase progress callback payload. Phases call progress(event) per file processed.

    Refactor 2 foundation: a single push-based shape every phase can emit so the
    Textual TUI, the daemon's WebSocket bridge, and CLI stdout printers all
    consume the same struct. ``error=True`` flags a per-file failure that did not
    abort the phase — fatal errors raise instead.
    """

    phase: str  # "scan" | "dedupe" | "lookup" | "apply" | "organize" | ...
    current: int
    total: int
    path: str = ""
    message: str = ""
    error: bool = False


class SkipReason(str, Enum):
    """Closed taxonomy of permanent-skip reasons. See optimization.md §CORE-2.

    String-valued so the value can be persisted directly to ``canonical.csv``'s
    nullable ``skip_reason`` column without an encoder, and round-tripped via
    ``SkipReason(value)`` on read.
    """

    LOSSY_ONLY_ON_APPLE = "lossy_only_on_apple"
    ALAC_LISTED_NOT_SERVABLE = "alac_listed_not_servable"
    REMIX_DJ_NOT_ON_APPLE = "remix_dj_not_on_apple"
    WRONG_MATCH_PERMANENT = "wrong_match_permanent"
    SHAZAM_NO_MATCH = "shazam_no_match"
    REGION_BLOCKED = "region_blocked"
    SITE_JUNK_METADATA = "site_junk_metadata"
    ACOUSTIC_DIALOG_ANIME = "acoustic_dialog_anime"


@dataclass
class ResolvedTrack:
    """Reconciled metadata record output by the resolve phase.

    Holds the folder+tag fusion for one dedupe winner. ``needs_lookup``
    is a ``+``-joined string of missing fields (e.g. ``"year+album"``)
    that downstream lookup tiers should attempt to fill.
    """

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
    """Best-match record returned by a single lookup tier.

    ``confidence`` is in [0, 1]. ``score_parts`` carries the weighted
    sub-scores (title, artist, duration, album bonus, penalties) for review
    CSVs and audit trails. ``raw`` is the tier's unmodified API response.
    """

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
    """Counters returned by :func:`musicorg.apply_canonical` and :func:`musicorg.apply_approvals`.

    ``guarded`` counts rows where the year-guardrail overrode the API year to
    protect the original album release (see _organizer/optimization.md §CORE-8).
    ``snapshot_path`` and ``undo_script_path`` point to the generated undo
    artifacts, or None when no rows were processed.
    """

    files_touched: int = 0
    tags_written: int = 0
    renamed: int = 0
    guarded: int = 0
    collisions: int = 0
    errors: int = 0
    snapshot_path: Path | None = None
    undo_script_path: Path | None = None
