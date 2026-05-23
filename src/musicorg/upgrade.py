"""Lossless ALAC upgrade via gamdl, driven by Apple Music URLs from Shazam.

Wraps the gamdl subprocess in a per-track orchestrator with ffprobe-based
lossless verification (audioTraits cannot be trusted alone — 26 files in
the reference run had `audioTraits: lossless` but no actual lossless stream).
Each run uses a unique staging subdir to defeat gamdl's idempotency trap
(it exits 0 without re-downloading when the output is already present).
"""

from __future__ import annotations

import csv
import datetime
import json
import logging
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .models import ProgressEvent


logger = logging.getLogger(__name__)


GAMDL_TIMEOUT_SEC = 600

PERMANENT_SKIP_REASONS: tuple[str, ...] = (
    "lossy_only_on_apple_music",
    "alac_listed_but_not_servable",
    "remix_dj_not_on_apple",
    "wrong_match_permanent",
    "shazam_no_match",
)

SKIPS_CSV_FIELDS = (
    "timestamp",
    "path",
    "apple_music_url",
    "adam_id",
    "storefront",
    "reason",
    "detail",
)

_ADAM_RX = re.compile(r"[?&]i=(\d+)")
_STOREFRONT_RX = re.compile(r"https?://music\.apple\.com/([a-z]{2})/", re.IGNORECASE)


def normalize_url(raw_url: str) -> str:
    """Canonicalise an Apple Music URL.

    Rewrites ``intent://music.apple.com/...`` to ``https://``, strips Android
    intent ``#Intent;...`` tails, drops ``/subscribe`` deeplinks (no track info),
    and requires song-level ``?i=<adam_id>``. Returns ``""`` when the URL is
    not a usable song-level Apple Music URL.
    """
    if not raw_url:
        return ""
    u = raw_url.strip()
    if u.startswith("intent://"):
        u = "https://" + u[len("intent://"):]
    u = u.split("#Intent;")[0]
    if "/subscribe" in u:
        return ""
    if "music.apple.com" not in u:
        return ""
    if not _ADAM_RX.search(u):
        return ""
    return u


def parse_apple_url(url: str) -> tuple[str, str]:
    """Return ``(adam_id, storefront)`` from a normalised Apple Music URL."""
    if not url:
        return "", ""
    adam_m = _ADAM_RX.search(url)
    adam_id = adam_m.group(1) if adam_m else ""
    sf_m = _STOREFRONT_RX.match(url)
    storefront = sf_m.group(1).lower() if sf_m else ""
    return adam_id, storefront


def find_new_staged(staging_dir: Path, before_snapshot: set[Path]) -> list[Path]:
    """Return .m4a/.flac files in ``staging_dir`` not present in the snapshot.

    The caller MUST pass a per-run unique staging subdir — gamdl exits 0 without
    re-downloading when its output already exists (§3.24 idempotency trap), so
    sharing a staging dir across runs silently masks failed downloads.
    """
    if not staging_dir.exists():
        return []
    after: set[Path] = set()
    for ext in ("*.m4a", "*.flac"):
        after.update(staging_dir.rglob(ext))
    new = sorted(after - before_snapshot, key=lambda p: p.stat().st_mtime)
    return new


def has_lossless_via_ffprobe(path: Path) -> bool:
    """Authoritative lossless check via ffprobe.

    True iff the first audio stream has ``codec_name`` in {alac, flac} AND
    ``bits_per_raw_sample`` (falling back to ``bits_per_sample``) is >= 16.
    Never trust Apple's ``audioTraits: lossless`` flag alone — observed false
    positives in production.
    """
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-print_format", "json",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if r.returncode != 0:
        return False
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return False
    audio = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
        None,
    )
    if not audio:
        return False
    codec = (audio.get("codec_name") or "").lower()
    if codec not in {"alac", "flac"}:
        return False
    bits_raw = audio.get("bits_per_raw_sample")
    bits_sample = audio.get("bits_per_sample")
    try:
        bits = int(bits_raw) if bits_raw not in (None, "", "0") else int(bits_sample or 0)
    except (TypeError, ValueError):
        bits = 0
    return bits >= 16


def run_gamdl(
    apple_music_url: str,
    staging_dir: Path,
    cookies_path: Path | None = None,
    wvd_path: Path | None = None,
) -> tuple[int, str, str]:
    """Invoke ``gamdl`` and return ``(returncode, stdout, stderr)``.

    Does not raise on non-zero exit — callers inspect the rc. Timeout is
    capped at 600s per track; a timeout returns ``(-1, "", "timeout")``.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = ["gamdl", "--output-path", str(staging_dir)]
    if cookies_path is not None:
        cmd += ["--cookies-path", str(cookies_path)]
    if wvd_path is not None:
        cmd += ["--wvd-path", str(wvd_path)]
    cmd.append(apple_music_url)
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GAMDL_TIMEOUT_SEC,
        )
        if r.returncode != 0:
            logger.warning(
                "gamdl exited %s for %s: %s",
                r.returncode,
                apple_music_url,
                (r.stderr or "")[-300:].strip(),
            )
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        logger.warning(
            "gamdl timed out after %ss for %s",
            GAMDL_TIMEOUT_SEC,
            apple_music_url,
        )
        return -1, "", "timeout"
    except FileNotFoundError as e:
        logger.error("gamdl binary not found on PATH: %s", e)
        return -2, "", f"gamdl-not-found: {e}"


def _snapshot_staging(staging_dir: Path) -> set[Path]:
    if not staging_dir.exists():
        return set()
    out: set[Path] = set()
    for ext in ("*.m4a", "*.flac"):
        out.update(staging_dir.rglob(ext))
    return out


def _pick_audio_from_new(new_files: list[Path]) -> Path | None:
    m4as = [p for p in new_files if p.suffix.lower() == ".m4a"]
    if m4as:
        return max(m4as, key=lambda p: p.stat().st_mtime)
    return None


def _quarantine_path(track_path: Path, music_root: Path) -> Path:
    """Build the ``_replaced/<rel>`` quarantine target for a music-root file."""
    try:
        rel = track_path.resolve().relative_to(music_root.resolve())
    except ValueError:
        # Fallback when track is outside music_root — use just the filename.
        rel = Path(track_path.name)
    return music_root / "Music" / "_replaced" / rel


def upgrade_one(
    track_path: Path,
    apple_music_url: str,
    *,
    staging_run_dir: Path,
    cookies_path: Path | None,
    wvd_path: Path | None,
    music_root: Path,
) -> dict:
    """Orchestrate a single lossy → ALAC upgrade for ``track_path``.

    Returns a status dict. Possible ``status`` values:
      - ``upgraded`` — new ALAC placed; original quarantined.
      - ``no_new_file`` — gamdl produced no new file in staging.
      - ``lossy_only_on_apple_music`` — gamdl returned a file but not ALAC/FLAC.
      - ``gamdl_failed`` — non-zero return code (transient).
      - ``original_missing`` — track vanished between scan and upgrade.
      - ``target_collision`` — a file at the target path already exists.
    """
    if not track_path.exists():
        return {"status": "original_missing", "path": str(track_path)}

    before = _snapshot_staging(staging_run_dir)
    rc, stdout, stderr = run_gamdl(
        apple_music_url, staging_run_dir, cookies_path, wvd_path
    )
    if rc != 0:
        return {
            "status": "gamdl_failed",
            "returncode": rc,
            "stderr_tail": (stderr or "")[-300:],
            "apple_music_url": apple_music_url,
        }

    new_files = find_new_staged(staging_run_dir, before)
    new_audio = _pick_audio_from_new(new_files)
    if new_audio is None:
        return {"status": "no_new_file", "apple_music_url": apple_music_url}

    if not has_lossless_via_ffprobe(new_audio):
        return {
            "status": "lossy_only_on_apple_music",
            "apple_music_url": apple_music_url,
            "staged_path": str(new_audio),
        }

    # Place new ALAC beside the original (.m4a, same stem); quarantine original.
    if not track_path.exists():
        return {"status": "original_missing", "path": str(track_path)}

    target = track_path.parent / (track_path.stem + ".m4a")
    quar = _quarantine_path(track_path, music_root)
    if quar.exists():
        return {
            "status": "target_collision",
            "detail": "quarantine_path_exists",
            "quarantine_path": str(quar),
        }
    if target.exists() and target.resolve() != track_path.resolve():
        return {
            "status": "target_collision",
            "detail": "target_path_exists",
            "target_path": str(target),
        }

    quar.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(track_path), str(quar))
    try:
        # Copy the staged file in (caller decides retention of staging artifact);
        # use copy2 to preserve metadata, then unlink the source on success.
        shutil.copy2(str(new_audio), str(target))
        new_audio.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(
            "copy of staged ALAC failed for %s: %s: %s",
            track_path,
            type(e).__name__,
            e,
        )
        # Roll back the quarantine move so we don't lose the original.
        try:
            shutil.move(str(quar), str(track_path))
        except Exception as rollback_exc:
            logger.error(
                "rollback of quarantine move failed for %s: %s: %s",
                track_path,
                type(rollback_exc).__name__,
                rollback_exc,
            )
        return {
            "status": "gamdl_failed",
            "detail": f"copy_failed: {type(e).__name__}: {e}",
            "apple_music_url": apple_music_url,
        }

    return {
        "status": "upgraded",
        "new_path": str(target),
        "replaced_path": str(quar),
        "apple_music_url": apple_music_url,
    }


def _write_skip_row(
    skips_csv: Path,
    candidate: dict,
    reason: str,
    detail: str = "",
) -> None:
    new_file = not skips_csv.exists()
    skips_csv.parent.mkdir(parents=True, exist_ok=True)
    with skips_csv.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SKIPS_CSV_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow({
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "path": str(candidate.get("path", "")),
            "apple_music_url": candidate.get("apple_music_url", ""),
            "adam_id": candidate.get("adam_id", ""),
            "storefront": candidate.get("storefront", ""),
            "reason": reason,
            "detail": detail,
        })


def _write_undo_script(undo_path: Path, ops: list[dict]) -> None:
    """Generate a stand-alone undo .py that restores quarantined originals."""
    body = (
        '#!/usr/bin/env python3\n'
        '"""Auto-generated undo for musicorg upgrade pass."""\n'
        'import shutil, sys\n'
        'from pathlib import Path\n\n'
        'OPS = ' + json.dumps(ops, indent=4) + '\n\n'
        'def main() -> int:\n'
        '    ok = fail = 0\n'
        '    for op in OPS:\n'
        '        new_path = Path(op["new_path"])\n'
        '        replaced = Path(op["replaced_path"])\n'
        '        original = Path(op["original_path"])\n'
        '        try:\n'
        '            if new_path.exists():\n'
        '                new_path.unlink()\n'
        '            if replaced.exists():\n'
        '                original.parent.mkdir(parents=True, exist_ok=True)\n'
        '                shutil.move(str(replaced), str(original))\n'
        '            ok += 1\n'
        '        except Exception as e:\n'
        '            print(f"undo failed for {original}: {e}", file=sys.stderr)\n'
        '            fail += 1\n'
        '    print(f"restored: {ok} | failed: {fail}")\n'
        '    return 0 if fail == 0 else 1\n\n'
        'if __name__ == "__main__":\n'
        '    raise SystemExit(main())\n'
    )
    undo_path.parent.mkdir(parents=True, exist_ok=True)
    undo_path.write_text(body)
    try:
        undo_path.chmod(0o755)
    except OSError:
        pass


def upgrade_batch(
    candidates: list[dict],
    music_root: Path,
    state_dir: Path,
    *,
    dry_run: bool = False,
    cookies_path: Path | None = None,
    wvd_path: Path | None = None,
    progress: Callable[[ProgressEvent], None] | None = None,
) -> dict:
    """Run ``upgrade_one`` over a list of candidates and emit logs + undo.

    Each candidate is a dict with keys ``path``, ``apple_music_url``,
    ``adam_id``, ``storefront``. Uses a per-run staging subdir under
    ``state_dir/_upgrade_staging/<run_id>`` so gamdl cannot short-circuit on
    a pre-existing output file. Permanent skips land in
    ``state_dir/upgrade_skips.csv``; an undo script for successful upgrades
    is written to ``state_dir/undo_upgrade_<TS>.py``. ``progress`` (if given)
    is invoked once per candidate with a ``ProgressEvent`` carrying the
    upgrade outcome in ``message``.
    """
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_id = f"run_{ts}"
    staging_root = state_dir / "_upgrade_staging"
    staging_run_dir = staging_root / run_id
    skips_csv = state_dir / "upgrade_skips.csv"
    undo_path = state_dir / f"undo_upgrade_{ts}.py"

    counts: dict[str, int] = {
        "candidates": len(candidates),
        "upgraded": 0,
        "no_new_file": 0,
        "lossy_only_on_apple_music": 0,
        "gamdl_failed": 0,
        "original_missing": 0,
        "target_collision": 0,
        "skipped_url_normalize": 0,
        "dry_run": 0,
    }
    undo_ops: list[dict] = []
    total = len(candidates)

    _OK_STATUSES = {"upgraded", "skipped", "dry_run"}

    def _emit(i: int, p: str, message: str, *, is_error: bool = False) -> None:
        if progress is None:
            return
        progress(ProgressEvent(
            phase="upgrade",
            current=i,
            total=total,
            path=p,
            message=message,
            error=is_error,
        ))

    for i, cand in enumerate(candidates, start=1):
        raw_url = (cand.get("apple_music_url") or "").strip()
        url = normalize_url(raw_url)
        path = Path(cand.get("path", ""))
        if not url:
            counts["skipped_url_normalize"] += 1
            _write_skip_row(skips_csv, cand, "wrong_match_permanent", "url_normalize_empty")
            logger.warning("skipped candidate %s: url did not normalize (raw=%r)", path, raw_url)
            _emit(i, str(path), "skipped_url_normalize", is_error=True)
            continue

        if dry_run:
            counts["dry_run"] += 1
            _emit(i, str(path), "dry_run")
            continue

        result = upgrade_one(
            path,
            url,
            staging_run_dir=staging_run_dir,
            cookies_path=cookies_path,
            wvd_path=wvd_path,
            music_root=music_root,
        )
        status = result.get("status", "")
        counts[status] = counts.get(status, 0) + 1

        if status == "upgraded":
            undo_ops.append({
                "original_path": str(path),
                "new_path": result["new_path"],
                "replaced_path": result["replaced_path"],
            })
            _write_undo_script(undo_path, undo_ops)
            logger.info("upgraded %s -> %s", path, result["new_path"])
        elif status == "lossy_only_on_apple_music":
            _write_skip_row(skips_csv, cand, "lossy_only_on_apple_music",
                            result.get("staged_path", ""))
            logger.info("permanent skip (lossy_only_on_apple_music) for %s", path)
        elif status == "no_new_file":
            # Could be transient (gamdl 404) or permanent (region-locked); the
            # rc was already 0, so treat as alac_listed_but_not_servable.
            _write_skip_row(skips_csv, cand, "alac_listed_but_not_servable",
                            "gamdl_rc0_no_staged_file")
            logger.info("permanent skip (alac_listed_but_not_servable) for %s", path)
        else:
            # gamdl_failed / target_collision / original_missing are not
            # permanent skips — leave them to the resumable state of the next run.
            logger.warning(
                "non-permanent upgrade failure for %s: status=%s detail=%s",
                path,
                status,
                result.get("detail") or result.get("stderr_tail") or "",
            )

        _emit(
            i,
            str(path),
            status,
            is_error=status not in _OK_STATUSES,
        )

    summary: dict[str, Any] = {
        "run_id": run_id,
        "staging_dir": str(staging_run_dir),
        "skips_csv": str(skips_csv),
        "undo_path": str(undo_path) if undo_ops else "",
        "counts": counts,
    }
    return summary


def permanent_skip_report(state_dir: Path) -> dict[str, list[dict]]:
    """Read ``upgrade_skips.csv`` and group rows by ``reason``.

    Returns an empty dict (keyed by the canonical taxonomy) if the file is
    missing — callers can treat absence the same as a clean slate.
    """
    out: dict[str, list[dict]] = {r: [] for r in PERMANENT_SKIP_REASONS}
    skips_csv = state_dir / "upgrade_skips.csv"
    if not skips_csv.exists():
        return out
    with skips_csv.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            reason = (row.get("reason") or "").strip()
            if reason not in out:
                out[reason] = []
            out[reason].append(dict(row))
    return out
