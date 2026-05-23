"""Shazam-driven re-fingerprint pass on lossy files.

Shazam is fingerprint-based, so it correctly IDs tracks even when filename
and tags are pure garbage — the long tail that text-based tiers miss. We
apply Shazam's canonical tags + rename regardless of whether an Apple Music
URL is harvested, because clean metadata is valuable independent of any
later lossless upgrade (plan §3.27).
"""

from __future__ import annotations

import asyncio
import csv
import datetime
import logging
import shutil
import time
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from . import tags as tagslib
from .clean import safe_filename
from .config import DEFAULT_EXCLUDE_DIR_NAMES
from .lookup import shazam as shazam_lookup
from .lookup.breaker import CircuitBreaker
from .models import ProgressEvent, TierMatch


logger = logging.getLogger(__name__)


REFINGERPRINT_CSV_FIELDS = (
    "timestamp",
    "path",
    "new_path",
    "shazam_title",
    "shazam_artist",
    "shazam_album",
    "shazam_year",
    "shazam_genre",
    "confidence",
    "apple_music_url",
    "adam_id",
    "storefront",
    "applied",
    "error",
)

SHAZAM_AUTO_APPLY_THRESHOLD = 0.50

EXCLUDE_DIR_NAMES = set(DEFAULT_EXCLUDE_DIR_NAMES) | {
    "_replaced",
    "_upgrade_staging",
    "_duplicates",
    "_misc",
}


def _iter_lossy_targets(music_root: Path, path_filter: Path | None) -> list[Path]:
    """Walk ``music_root`` for lossy audio, skipping organizer-owned dirs."""
    base = path_filter if path_filter is not None else music_root
    targets: list[Path] = []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in tagslib.LOSSY_EXTS:
            continue
        if any(seg in EXCLUDE_DIR_NAMES for seg in p.parts):
            continue
        targets.append(p)
    targets.sort()
    return targets


def _existing_track_num(path: Path) -> str:
    """Recover a 1-99 track number from the filename prefix only.

    We deliberately skip the tag here — the lossy tag is the thing we are
    about to overwrite, so trusting it would be circular.
    """
    import re

    m = re.match(r"^\s*\(?(\d{1,3})\)?[\s._-]+", path.name)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 99:
            return str(n)
    return ""


def _apply_shazam_match(path: Path, match: TierMatch) -> Path:
    """Write Shazam tags onto ``path`` and rename it to canonical form.

    Returns the final path. Rename is collision-safe via " (N)" suffix. The
    file stays in its current directory — folder relocation is the planner's
    job, not the refingerprint pass.
    """
    track_n = _existing_track_num(path)
    fields = {
        "title": match.title,
        "artist": match.artist,
        "albumartist": match.artist,
        "album": match.album,
        "year": (match.year or "")[:4],
        "track": track_n,
        "genre": match.genre,
    }
    tagslib.write(path, fields)

    ext = path.suffix.lower()
    safe_title = safe_filename(match.title) or path.stem
    if track_n:
        new_name = f"{int(track_n):02d} - {safe_title}{ext}"
    else:
        new_name = f"{safe_title}{ext}"
    new_path = path.parent / new_name

    if new_path == path:
        return path

    candidate = new_path
    base, e = new_path.stem, new_path.suffix
    j = 2
    while candidate.exists() and candidate != path:
        candidate = path.parent / f"{base} ({j}){e}"
        j += 1
    if candidate == path:
        return path
    shutil.move(str(path), str(candidate))
    return candidate


def _append_refingerprint_row(out_csv: Path, row: dict[str, Any]) -> None:
    new_file = not out_csv.exists()
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REFINGERPRINT_CSV_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in REFINGERPRINT_CSV_FIELDS})


def _recognize_sync(path: Path) -> TierMatch | None:
    """Bridge between shazam_lookup's async/sync calling conventions.

    The lookup module exposes ``recognize(path) -> TierMatch | None``; if the
    implementation is async we drive it via ``asyncio.run``. Either way the
    return type is the same.
    """
    rec = shazam_lookup.recognize
    if asyncio.iscoroutinefunction(rec):
        return asyncio.run(rec(path))
    result = rec(path)
    if asyncio.iscoroutine(result):
        return asyncio.run(result)  # type: ignore[arg-type]
    return result


def refingerprint_lossy(
    music_root: Path,
    state_dir: Path,
    *,
    lossy_only: bool = True,
    path_filter: Path | None = None,
    breaker: CircuitBreaker | None = None,
    sleep_sec: float = 1.5,
    progress: Callable[[ProgressEvent], None] | None = None,
) -> dict:
    """Walk ``music_root``, Shazam-fingerprint each lossy file, apply + log.

    For every match with score >= ``SHAZAM_AUTO_APPLY_THRESHOLD`` (0.50,
    intentionally loose — Shazam is audio ID, not text similarity), write
    canonical tags + rename in place. Rows that also carry an Apple Music URL
    feed the subsequent upgrade pass via
    ``state_dir/30_shazam_refingerprint.csv``.

    Respects ``breaker`` if provided: a tripped marker short-circuits the
    entire phase; consecutive recognize() failures are recorded so the
    breaker can trip itself.
    """
    out_csv = state_dir / "30_shazam_refingerprint.csv"

    if breaker is not None:
        marker = breaker.is_tripped()
        if marker is not None:
            logger.warning(
                "shazam breaker tripped (marker=%s) — skipping refingerprint phase",
                marker,
            )
            return {
                "status": "skipped_breaker_tripped",
                "marker": str(marker),
                "scanned": 0,
                "matched": 0,
                "applied": 0,
                "with_apple_url": 0,
                "errors": 0,
            }

    if not lossy_only:
        # Reserved for a future "re-check m4a/flac" mode; default path is lossy.
        logger.warning("lossy_only=False is not yet supported; ignoring")

    targets = _iter_lossy_targets(music_root, path_filter)

    counts = {
        "scanned": 0,
        "matched": 0,
        "applied": 0,
        "no_match": 0,
        "with_apple_url": 0,
        "errors": 0,
        "low_confidence": 0,
    }

    total = len(targets)

    def _emit(i: int, p: Path, status: str, is_error: bool = False) -> None:
        if progress is None:
            return
        progress(ProgressEvent(
            phase="refingerprint",
            current=i,
            total=total,
            path=str(p),
            message=status,
            error=is_error,
        ))

    for i, path in enumerate(targets, start=1):
        counts["scanned"] += 1
        if not path.exists():
            _emit(i, path, "missing")
            continue

        try:
            match = _recognize_sync(path)
        except Exception as e:
            counts["errors"] += 1
            err = f"{type(e).__name__}: {e}"
            logger.warning("recognize failed for %s: %s", path, err)
            _append_refingerprint_row(out_csv, {
                "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                "path": str(path),
                "error": err[:200],
                "applied": "0",
            })
            _emit(i, path, "recognize_error", is_error=True)
            if breaker is not None:
                tripped = breaker.record_failure(err)
                if tripped:
                    logger.error("shazam breaker tripped after %s", path)
                    return {
                        "status": "breaker_tripped_during_run",
                        "counts": counts,
                    }
            time.sleep(sleep_sec)
            continue

        if breaker is not None:
            breaker.record_success()

        if match is None or not (match.title or "").strip():
            counts["no_match"] += 1
            _append_refingerprint_row(out_csv, {
                "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                "path": str(path),
                "applied": "0",
                "error": "shazam_no_match",
            })
            _emit(i, path, "no_match")
            time.sleep(sleep_sec)
            continue

        counts["matched"] += 1

        if match.confidence < SHAZAM_AUTO_APPLY_THRESHOLD:
            counts["low_confidence"] += 1
            _append_refingerprint_row(out_csv, {
                "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                "path": str(path),
                "shazam_title": match.title,
                "shazam_artist": match.artist,
                "shazam_album": match.album,
                "shazam_year": match.year,
                "shazam_genre": match.genre,
                "confidence": f"{match.confidence:.3f}",
                "apple_music_url": match.apple_music_url,
                "adam_id": match.adam_id,
                "storefront": match.storefront,
                "applied": "0",
            })
            _emit(i, path, "low_confidence")
            time.sleep(sleep_sec)
            continue

        applied_path = path
        applied = False
        err = ""
        try:
            applied_path = _apply_shazam_match(path, match)
            applied = True
            counts["applied"] += 1
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            counts["errors"] += 1
            logger.warning("apply failed for %s: %s", path, err)

        if match.apple_music_url:
            counts["with_apple_url"] += 1

        _append_refingerprint_row(out_csv, {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "path": str(path),
            "new_path": str(applied_path) if applied else "",
            "shazam_title": match.title,
            "shazam_artist": match.artist,
            "shazam_album": match.album,
            "shazam_year": match.year,
            "shazam_genre": match.genre,
            "confidence": f"{match.confidence:.3f}",
            "apple_music_url": match.apple_music_url,
            "adam_id": match.adam_id,
            "storefront": match.storefront,
            "applied": "1" if applied else "0",
            "error": err,
        })

        _emit(i, path, "applied" if applied else "apply_error", is_error=not applied)

        time.sleep(sleep_sec)

    return {"status": "ok", "csv_path": str(out_csv), "counts": counts}


def _title_key(s: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _candidate_lossy_files(music_root: Path) -> list[Path]:
    """Lossy files still under ``music_root``, ignoring organizer-owned dirs."""
    out: list[Path] = []
    for p in music_root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in tagslib.LOSSY_EXTS:
            continue
        if any(seg in EXCLUDE_DIR_NAMES for seg in p.parts):
            continue
        out.append(p)
    return out


def recover_staging(
    state_dir: Path,
    music_root: Path,
    *,
    dry_run: bool = False,
    fuzz_threshold: float = 0.85,
) -> dict:
    """Reconcile orphan ALAC files left behind by an interrupted upgrade run.

    Scans ``state_dir/_upgrade_staging`` recursively for .m4a files, then
    fuzzy-matches each against the current lossy library by title similarity
    (``difflib.SequenceMatcher`` on the stems). On a match >= ``fuzz_threshold``
    the ALAC is placed beside the lossy original and the lossy file is moved
    into ``music_root/Music/_replaced/``. ``dry_run=True`` reports the plan
    without touching anything.
    """
    staging_root = state_dir / "_upgrade_staging"
    summary: dict[str, Any] = {
        "scanned": 0,
        "matched": 0,
        "placed": 0,
        "unmatched": [],  # list[dict]
        "dry_run": dry_run,
    }
    if not staging_root.exists():
        return summary

    orphans = [p for p in staging_root.rglob("*.m4a") if p.is_file()]
    summary["scanned"] = len(orphans)
    if not orphans:
        return summary

    library = _candidate_lossy_files(music_root)
    lib_keys = [(p, _title_key(p.stem)) for p in library]

    for orphan in orphans:
        orphan_key = _title_key(orphan.stem)
        if not orphan_key:
            summary["unmatched"].append({"orphan": str(orphan), "reason": "empty_key"})
            continue

        best: tuple[float, Path | None] = (0.0, None)
        for lib_path, key in lib_keys:
            if not key:
                continue
            ratio = SequenceMatcher(None, orphan_key, key).ratio()
            if ratio > best[0]:
                best = (ratio, lib_path)

        score, lib_path = best
        if lib_path is None or score < fuzz_threshold:
            summary["unmatched"].append({
                "orphan": str(orphan),
                "best_score": round(score, 3),
                "best_match": str(lib_path) if lib_path else "",
            })
            continue

        summary["matched"] += 1
        target = lib_path.parent / (lib_path.stem + ".m4a")
        try:
            rel = lib_path.resolve().relative_to(music_root.resolve())
        except ValueError:
            rel = Path(lib_path.name)
        quar = music_root / "Music" / "_replaced" / rel

        if dry_run:
            logger.info(
                "recover plan: %s -> %s (quarantine %s, score=%.3f)",
                orphan, target, quar, score,
            )
            continue

        if quar.exists():
            summary["unmatched"].append({
                "orphan": str(orphan),
                "reason": "quarantine_collision",
                "quarantine_path": str(quar),
            })
            continue
        if target.exists() and target.resolve() != lib_path.resolve():
            summary["unmatched"].append({
                "orphan": str(orphan),
                "reason": "target_collision",
                "target_path": str(target),
            })
            continue

        quar.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(lib_path), str(quar))
            shutil.move(str(orphan), str(target))
            summary["placed"] += 1
        except Exception as e:
            logger.error("recover failed for %s: %s", orphan, e)
            # Best-effort rollback of the quarantine move.
            if quar.exists() and not lib_path.exists():
                try:
                    shutil.move(str(quar), str(lib_path))
                except Exception:
                    pass
            summary["unmatched"].append({
                "orphan": str(orphan),
                "reason": f"move_error: {type(e).__name__}: {e}",
            })

    return summary
