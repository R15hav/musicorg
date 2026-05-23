"""Canonical-metadata lookup tiers + cascade orchestrator.

The ``chain()`` entry point runs iTunes → JioSaavn → Shazam in order,
short-circuiting as soon as a tier returns a confidence at-or-above its
auto-apply threshold. Each tier is independently importable so callers (CLI,
tests) can also invoke them in isolation.
"""

from __future__ import annotations

from pathlib import Path

from ..clean import parse_folder, title_from_filename
from ..config import Config
from ..models import TierMatch, Track
from .breaker import CircuitBreaker
from .itunes import search as itunes_search
from .jiosaavn import search as jiosaavn_search
from .scoring import decide, score_match
from .shazam import (
    extract_apple_music_url,
    recognize as shazam_recognize,
    score_shazam,
)


__all__ = [
    "CircuitBreaker",
    "chain",
    "decide",
    "extract_apple_music_url",
    "itunes_search",
    "jiosaavn_search",
    "score_match",
    "score_shazam",
    "shazam_recognize",
]


def _folder_context(track: Track) -> tuple[str, str]:
    parent = track.path.parent.name if track.path else ""
    return parse_folder(parent)


def chain(track: Track, cfg: Config) -> tuple[TierMatch | None, str]:
    """Run iTunes → JioSaavn → Shazam in cascade against ``track``.

    Short-circuits at the first tier whose confidence meets its auto-apply
    threshold (``cfg.shazam_auto_apply_threshold`` for Shazam,
    ``cfg.auto_apply_threshold`` for the others). The Shazam tier is skipped
    if its circuit-breaker marker exists in ``cfg.state_path``.

    Returns ``(best_match, decision)`` where ``decision`` is one of
    ``"auto_apply" | "review" | "low" | "no_match"``. ``best_match`` is the
    highest-confidence TierMatch seen across tiers, or ``None`` if nothing
    matched.
    """
    album_hint, folder_year = _folder_context(track)
    cur_title = track.title or title_from_filename(track.path.name if track.path else "")
    cur_artist = track.artist or ""
    duration = float(track.duration_sec or 0.0)

    best: TierMatch | None = None

    itunes_match = itunes_search(
        title=cur_title,
        artist=cur_artist,
        album_hint=album_hint,
        folder_year=folder_year,
        duration_sec=duration,
        country=cfg.itunes_country,
    )
    if itunes_match is not None:
        best = itunes_match
        if itunes_match.confidence >= cfg.auto_apply_threshold:
            return itunes_match, "auto_apply"

    jio_match = jiosaavn_search(
        title=cur_title,
        artist=cur_artist,
        album_hint=album_hint,
        folder_year=folder_year,
        user_agent=cfg.jiosaavn_user_agent,
    )
    if jio_match is not None:
        if best is None or jio_match.confidence > best.confidence:
            best = jio_match
        if jio_match.confidence >= cfg.auto_apply_threshold:
            return jio_match, "auto_apply"

    breaker = CircuitBreaker(
        name="SHAZAMIO",
        threshold=cfg.shazam_breaker_threshold,
        marker_dir=Path(cfg.state_dir) if cfg.state_dir else Path.cwd(),
    )
    if breaker.is_tripped() is None:
        try:
            sh = shazam_recognize(track.path)
        except ImportError:
            sh = None
        except Exception:
            # The caller (canonicalize) is responsible for updating the
            # breaker; we never poke at it from inside chain().
            sh = None
        if sh is not None:
            sh.confidence = score_shazam(sh.title, sh.artist, cur_title, cur_artist)
            if best is None or sh.confidence > best.confidence:
                best = sh
            if sh.confidence >= cfg.shazam_auto_apply_threshold:
                return sh, "auto_apply"

    if best is None:
        return None, "no_match"
    return best, decide(
        best.confidence,
        threshold_auto=cfg.auto_apply_threshold,
        threshold_review=cfg.review_threshold,
    )
