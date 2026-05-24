"""Workers for metadata canonicalization + apply.

Three workers:

- :class:`CanonicalizeWorker` runs the library's default tiered lookup
  (:func:`musicorg.canonicalize_library`) over the organized music tree
  and writes ``16_merged.csv``. Tier order: iTunes → JioSaavn → Shazam.
- :class:`CustomOrderCanonicalizeWorker` runs a JioSaavn → iTunes →
  Shazam cascade by orchestrating the per-tier search functions
  directly. Writes the same ``16_merged.csv`` schema; additionally
  populates the per-tier columns (``jio_*`` / ``api_*`` / ``shazam_*``)
  for whichever tier produced the winning match.
- :class:`ApplyApprovalsWorker` consumes a generated
  ``19_approvals.json`` and rewrites tags via
  :func:`musicorg.apply_approvals`.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any, Callable

import musicorg
from musicorg import (
    ApplyResult,
    Config,
    ProgressEvent,
    apply_approvals,
    canonicalize_library,
)
from musicorg.lookup import decide
from musicorg.tags import AUDIO_EXTS, read as read_tags
from musicorg.config import DEFAULT_EXCLUDE_DIR_NAMES
from musicorg.clean import parse_folder, title_from_filename

# Private dep: pinning the merged-CSV schema. Kept in lock-step with
# musicorg.canonicalize._MERGED_FIELDS — same list, same order.
from musicorg.canonicalize import _MERGED_FIELDS  # noqa: PLC2701  (private import)

# Extra columns the custom-order worker writes on top of the canonical schema.
# These let the review UI display every tier that returned a match — not just
# the winner — without losing data when a non-winning tier is later picked.
# ``apply_approvals`` ignores unknown columns (DictReader is field-tolerant)
# so adding these is safe for the CLI / library callers.
_EXTRA_FIELDS: list[str] = [
    "winning_tier",
    "itunes_title", "itunes_artist", "itunes_album", "itunes_year",
    "itunes_track_num", "itunes_genre", "itunes_track_view_url",
    "itunes_confidence",
    "jio_confidence",
    "shazam_confidence",
]

# Field list the custom-order writer actually emits.
_CUSTOM_MERGED_FIELDS: list[str] = list(_MERGED_FIELDS) + _EXTRA_FIELDS

from .base import LibraryWorker


logger = logging.getLogger(__name__)


class CanonicalizeWorker(LibraryWorker):
    """Tiered iTunes → JioSaavn → Shazam lookup over the music tree."""

    def __init__(self, cfg: Config, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg = cfg

    def do_work(self, progress_cb: Callable[[ProgressEvent], None]) -> dict[str, int]:
        return canonicalize_library(self._cfg, progress=progress_cb)


class CustomOrderCanonicalizeWorker(LibraryWorker):
    """JioSaavn → iTunes → Shazam lookup over the music tree.

    Mirrors :func:`musicorg.canonicalize_library` but runs tiers in
    ``jiosaavn → itunes → shazam`` order — explicitly NOT the library
    default — and short-circuits whenever a tier returns a confidence at
    or above ``cfg.auto_apply_threshold``. When no tier crosses the
    auto-apply bar, the highest-confidence match across all probed tiers
    wins and its decision bucket is computed via :func:`musicorg.decide`.

    Each row in ``16_merged.csv`` carries the standard ``api_*`` columns
    (mirroring the winning tier's data so existing ``apply_approvals``
    with ``pick="itunes"`` keeps working) AND the per-tier columns for
    every tier that returned a candidate — useful for downstream UIs
    that want to show all options.
    """

    def __init__(self, cfg: Config, music_root: Path | None = None, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._music_root = music_root

    def do_work(self, progress_cb: Callable[[ProgressEvent], None]) -> dict[str, int]:
        cfg = self._cfg
        root = self._music_root if self._music_root is not None else (cfg.root_path / "Music")
        state_dir = cfg.state_path
        state_dir.mkdir(parents=True, exist_ok=True)
        merged_path = state_dir / "16_merged.csv"

        # Mirror ScanWorker's behaviour: skip the post-execute organize
        # tree at ``<root>/Music/`` on re-runs so we don't process every
        # track twice. The directory only matters as an exclude when it
        # actually exists (first run is unaffected).
        organized_root = (root / "Music").resolve()
        skip_organized = organized_root.exists()

        files: list[Path] = []
        if root.exists():
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in AUDIO_EXTS:
                    continue
                if any(part in DEFAULT_EXCLUDE_DIR_NAMES for part in p.parts):
                    continue
                if skip_organized:
                    try:
                        p.resolve().relative_to(organized_root)
                        continue  # under <root>/Music/ — exclude
                    except ValueError:
                        pass  # not under <root>/Music/ — keep
                files.append(p)
            files.sort()

        total = len(files)
        stats: dict[str, int] = {"auto_apply": 0, "review": 0, "low": 0, "no_match": 0}

        with merged_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_CUSTOM_MERGED_FIELDS, extrasaction="ignore")
            writer.writeheader()

            for i, p in enumerate(files, 1):
                try:
                    track = read_tags(p)
                except Exception as exc:
                    logger.warning("custom-order canonicalize: read_tags failed for %s: %s", p, exc)
                    progress_cb(ProgressEvent(
                        phase="canonicalize", current=i, total=total, path=str(p),
                        message=f"read_tags failed: {exc}", error=True,
                    ))
                    continue

                parent_name = p.parent.name
                album_hint, folder_year = parse_folder(parent_name)
                cur_title = track.title or title_from_filename(p.name)
                cur_artist = track.artist or ""
                duration = float(track.duration_sec or 0.0)

                candidates: list[tuple[str, Any]] = []
                auto_winner: tuple[str, Any] | None = None

                # Tier 1: JioSaavn ---------------------------------------------------
                jio = self._safe_jiosaavn(cur_title, cur_artist, album_hint, folder_year)
                if jio is not None:
                    candidates.append(("jiosaavn", jio))
                    if jio.confidence >= cfg.auto_apply_threshold:
                        auto_winner = ("jiosaavn", jio)

                # Tier 2: iTunes -----------------------------------------------------
                if auto_winner is None:
                    itu = self._safe_itunes(cur_title, cur_artist, album_hint, folder_year, duration)
                    if itu is not None:
                        candidates.append(("itunes", itu))
                        if itu.confidence >= cfg.auto_apply_threshold:
                            auto_winner = ("itunes", itu)

                # Tier 3: Shazam -----------------------------------------------------
                if auto_winner is None:
                    sh = self._safe_shazam(p, cur_title, cur_artist)
                    if sh is not None:
                        candidates.append(("shazam", sh))
                        if sh.confidence >= cfg.auto_apply_threshold:
                            auto_winner = ("shazam", sh)

                # Pick winner: short-circuit pick, else best by confidence -----------
                if auto_winner is not None:
                    winning_tier, best = auto_winner
                    decision = "auto_apply"
                elif candidates:
                    winning_tier, best = max(candidates, key=lambda kv: float(getattr(kv[1], "confidence", 0.0) or 0.0))
                    conf = float(getattr(best, "confidence", 0.0) or 0.0)
                    if conf >= cfg.auto_apply_threshold:
                        decision = "auto_apply"
                    elif conf >= cfg.review_threshold:
                        decision = "review"
                    elif conf > 0:
                        decision = "low"
                    else:
                        # Mirror the canonical decide() so behaviour stays in lock-step.
                        decision = decide(
                            conf,
                            threshold_auto=cfg.auto_apply_threshold,
                            threshold_review=cfg.review_threshold,
                        )
                else:
                    winning_tier, best = "", None
                    decision = "no_match"

                stats[decision] = stats.get(decision, 0) + 1

                row: dict[str, Any] = {
                    "source_path": str(p),
                    "decision": decision,
                    "confidence": float(getattr(best, "confidence", 0.0) or 0.0) if best is not None else 0.0,
                    "cur_title": track.title,
                    "cur_artist": track.artist,
                    "cur_album": track.album,
                    "cur_year": track.year,
                    "cur_track": track.track,
                    "cur_duration": track.duration_sec,
                }

                # Per-tier columns — populate whichever tiers returned a match.
                for tier_name, match in candidates:
                    self._fill_tier_columns(row, tier_name, match)

                # Record which tier produced the winning match so the review
                # UI can pre-select it and display tier ranking honestly.
                row["winning_tier"] = winning_tier

                # ``api_*`` mirrors the winning tier so existing apply_approvals
                # with pick="itunes" still works regardless of which tier won.
                if best is not None:
                    parts = getattr(best, "score_parts", None) or {}
                    row.update({
                        "api_title": getattr(best, "title", ""),
                        "api_artist": getattr(best, "artist", ""),
                        "api_album": getattr(best, "album", ""),
                        "api_year": getattr(best, "year", ""),
                        "api_track_num": getattr(best, "track_num", ""),
                        "api_genre": getattr(best, "genre", ""),
                        "api_track_view_url": getattr(best, "apple_music_url", ""),
                        "apple_music_url": getattr(best, "apple_music_url", ""),
                        "adam_id": getattr(best, "adam_id", ""),
                        "storefront": getattr(best, "storefront", ""),
                        "isrc": getattr(best, "isrc", ""),
                    })
                    for k, v in parts.items():
                        row[k] = v

                writer.writerow(row)

                # Progress callback -------------------------------------------------
                msg = f"{track.title or p.name}"
                if best is not None:
                    conf = float(getattr(best, "confidence", 0.0) or 0.0)
                    msg += f" → {winning_tier or '?'}: {getattr(best, 'title', '')} ({decision}, {conf:.2f})"
                else:
                    msg += f" → {decision}"
                progress_cb(ProgressEvent(
                    phase="canonicalize", current=i, total=total, path=str(p),
                    message=msg,
                ))

        return stats

    # ---- per-tier safe wrappers -------------------------------------------------
    def _safe_jiosaavn(self, title: str, artist: str, album_hint: str, folder_year: str) -> Any:
        try:
            return musicorg.jiosaavn_search(
                title=title,
                artist=artist,
                album_hint=album_hint,
                folder_year=folder_year,
                user_agent=self._cfg.jiosaavn_user_agent,
            )
        except Exception as exc:
            logger.warning("jiosaavn tier failed: %s", exc)
            return None

    def _safe_itunes(self, title: str, artist: str, album_hint: str, folder_year: str, duration: float) -> Any:
        try:
            return musicorg.itunes_search(
                title=title,
                artist=artist,
                album_hint=album_hint,
                folder_year=folder_year,
                duration_sec=duration,
                country=self._cfg.itunes_country,
            )
        except Exception as exc:
            logger.warning("itunes tier failed: %s", exc)
            return None

    def _safe_shazam(self, path: Path, cur_title: str, cur_artist: str) -> Any:
        try:
            sh = musicorg.shazam_recognize(path)
        except Exception as exc:
            logger.warning("shazam tier failed: %s", exc)
            return None
        if sh is None:
            return None
        try:
            sh.confidence = musicorg.score_shazam(sh.title, sh.artist, cur_title, cur_artist)
        except Exception as exc:
            logger.warning("shazam scoring failed: %s", exc)
        return sh

    # ---- per-tier column writers ------------------------------------------------
    @staticmethod
    def _fill_tier_columns(row: dict, tier: str, match: Any) -> None:
        conf = float(getattr(match, "confidence", 0.0) or 0.0)
        if tier == "jiosaavn":
            raw = getattr(match, "raw", {}) or {}
            row["jio_title"] = getattr(match, "title", "")
            row["jio_artist"] = getattr(match, "artist", "")
            row["jio_album"] = getattr(match, "album", "")
            row["jio_year"] = getattr(match, "year", "")
            row["jio_language"] = (raw.get("language") if isinstance(raw, dict) else "") or ""
            row["jio_perma_url"] = (raw.get("perma_url") if isinstance(raw, dict) else "") or ""
            row["jio_confidence"] = conf
        elif tier == "shazam":
            row["shazam_title"] = getattr(match, "title", "")
            row["shazam_artist"] = getattr(match, "artist", "")
            row["shazam_album"] = getattr(match, "album", "")
            row["shazam_year"] = getattr(match, "year", "")
            row["shazam_genre"] = getattr(match, "genre", "")
            row["shazam_isrc"] = getattr(match, "isrc", "")
            row["shazam_url"] = getattr(match, "apple_music_url", "")
            row["shazam_image"] = getattr(match, "cover_url", "")
            row["shazam_confidence"] = conf
        elif tier == "itunes":
            # Dedicated itunes_* columns let the review UI show iTunes data
            # even when iTunes was a candidate but didn't win. ``api_*``
            # still gets the winning tier's data — handled by the caller.
            row["itunes_title"] = getattr(match, "title", "")
            row["itunes_artist"] = getattr(match, "artist", "")
            row["itunes_album"] = getattr(match, "album", "")
            row["itunes_year"] = getattr(match, "year", "")
            row["itunes_track_num"] = getattr(match, "track_num", "")
            row["itunes_genre"] = getattr(match, "genre", "")
            row["itunes_track_view_url"] = getattr(match, "apple_music_url", "")
            row["itunes_confidence"] = conf


class ApplyApprovalsWorker(LibraryWorker):
    """Apply user approvals from an approvals JSON to the merged CSV."""

    def __init__(
        self,
        cfg: Config,
        approvals_json: Path,
        *,
        dry_run: bool = False,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._approvals_json = approvals_json
        self._dry_run = dry_run

    def do_work(self, progress_cb: Callable[[ProgressEvent], None]) -> ApplyResult:
        state = Path(self._cfg.state_dir)
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_path = state / "backups" / f"tag_snapshot_{ts}.json"
        undo_script_path = state / f"undo_phase18_{ts}.py"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)

        result = apply_approvals(
            state / "16_merged.csv",
            self._approvals_json,
            snapshot_path,
            undo_script_path,
            dry_run=self._dry_run,
            progress=progress_cb,
        )
        return result
