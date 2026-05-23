"""Canonical-metadata application layer.

Owns the dry-run diff (17), the apply step (18), and the post-approval
apply (20). The hard-won guardrail is here: when the parent folder is
``Album (YYYY)`` and the API year disagrees by 3+ years, we trust the
folder year and keep the current track number — iTunes returning a
compilation reissue used to silently rewrite original releases.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from . import backup, tags as tags_mod
from .clean import safe_filename, TRACK_PREFIX_RX
from .models import ApplyResult


_WRITABLE_EXTS = {".mp3", ".m4a", ".flac"}
_DEFAULT_SOURCE_PREFERENCE: list[str] = ["jiosaavn", "shazam", "itunes"]

_DIFF_FIELDS: list[str] = [
    "source_path", "decision_source", "kept_folder",
    "current_filename", "new_filename", "filename_changes",
    "title_old", "title_new",
    "artist_old", "artist_new",
    "album_old", "album_new",
    "year_old", "year_new",
    "track_old", "track_new",
    "albumartist_old", "albumartist_new",
    "genre_old", "genre_new",
    "n_field_changes",
]


def derive_track_num(api_track: str, current_track: str, current_filename: str) -> str:
    """Best-effort 1-99 track number.

    Priority: API track > current tag track > numeric filename prefix.
    Anything outside 1-99 is rejected (CDs and oddball numbering caused
    bogus 03-track-on-30-track-album writes in the original pipeline).
    """
    for src in (api_track, current_track):
        if src:
            m = re.match(r"^\s*(\d{1,3})", str(src))
            if m and 1 <= int(m.group(1)) <= 99:
                return str(int(m.group(1)))
    m = TRACK_PREFIX_RX.match(current_filename or "")
    if m and 1 <= int(m.group(1)) <= 99:
        return str(int(m.group(1)))
    return ""


def folder_year(parent_name: str) -> int:
    """Parse ``(YYYY)`` out of a folder name; returns 0 if absent."""
    if not parent_name:
        return 0
    m = re.match(r"^.*\((\d{4})\)\s*$", parent_name)
    return int(m.group(1)) if m else 0


def build_new_filename(api_title: str, track_num: str, ext: str) -> str:
    """``NN - Title.ext`` if track in 1-99, else ``Title.ext``."""
    title = safe_filename(api_title or "")
    if track_num and str(track_num).isdigit() and 1 <= int(track_num) <= 99:
        return safe_filename(f"{int(track_num):02d} - {title}{ext}")
    return safe_filename(f"{title}{ext}")


def _detect_source(row: dict) -> str:
    api_title = (row.get("api_title") or "").strip()
    if (row.get("shazam_title") or "").strip() and api_title == (row.get("shazam_title") or "").strip():
        return "shazam"
    if (row.get("jio_title") or "").strip() and api_title == (row.get("jio_title") or "").strip():
        return "jiosaavn"
    return "itunes"


def _read_current_tags(path: Path) -> dict[str, str]:
    """Lightweight read for diffing. Tolerates missing files."""
    if not path.exists():
        return {"title": "", "artist": "", "album": "", "albumartist": "",
                "year": "", "track": "", "genre": ""}
    try:
        track = tags_mod.read(path)
    except Exception:
        return {"title": "", "artist": "", "album": "", "albumartist": "",
                "year": "", "track": "", "genre": ""}
    return {
        "title": track.title or "",
        "artist": track.artist or "",
        "album": track.album or "",
        "albumartist": track.albumartist or "",
        "year": track.year or "",
        "track": track.track or "",
        "genre": track.genre or "",
    }


def _cmp(old: str, new: str) -> str:
    if not new:
        return ""
    if not old:
        return "set"
    return "keep" if old.strip() == new.strip() else "change"


def build_diff(
    merged_csv_path: Path,
    dryrun_csv_out: Path,
    promote_set: set[str] | None = None,
) -> dict:
    """Read the merged CSV, apply promotions, write the per-row diff CSV.

    Returns a summary dict with totals, promoted count, eligible count, and
    per-field action counters (``set`` / ``change`` / ``keep``) plus
    ``fn_rename`` / ``fn_keep`` filename action counters.
    """
    merged_csv_path = Path(merged_csv_path)
    dryrun_csv_out = Path(dryrun_csv_out)
    promote_set = promote_set or set()

    with merged_csv_path.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    promoted = 0
    for r in rows:
        if r.get("source_path") in promote_set and r.get("decision") != "auto_apply":
            r["decision"] = "auto_apply"
            promoted += 1

    eligible = [r for r in rows if r.get("decision") == "auto_apply"]

    summary: Counter = Counter()
    source_counts: Counter = Counter()

    dryrun_csv_out.parent.mkdir(parents=True, exist_ok=True)
    with dryrun_csv_out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_DIFF_FIELDS)
        w.writeheader()
        for r in eligible:
            path = Path(r.get("source_path", ""))
            current = _read_current_tags(path)
            current_fn = path.name
            ext = path.suffix.lower()

            api_title = (r.get("api_title") or "").strip()
            api_artist = (r.get("api_artist") or "").strip()
            api_album = (r.get("api_album") or "").strip()
            api_year = (r.get("api_year") or "").strip()[:4]
            api_track = (r.get("api_track_num") or "").strip()
            api_genre = (r.get("api_genre") or "").strip()

            track_num = derive_track_num(api_track, current.get("track", ""), current_fn)
            new_fn = build_new_filename(api_title or path.stem, track_num, ext)

            current_year = current.get("year", "")[:4] if current.get("year") else ""
            current_trk = (current.get("track", "") or "").split("/")[0]

            changes = {
                "title":       _cmp(current.get("title", ""), api_title),
                "artist":      _cmp(current.get("artist", ""), api_artist),
                "album":       _cmp(current.get("album", ""), api_album),
                "year":        _cmp(current_year, api_year),
                "track":       _cmp(current_trk, track_num),
                "albumartist": _cmp(current.get("albumartist", ""), api_artist),
                "genre":       _cmp(current.get("genre", ""), api_genre),
            }
            n_changes = sum(1 for v in changes.values() if v in ("set", "change"))
            fn_change = "rename" if new_fn != current_fn else "keep"

            for v in changes.values():
                summary[v] += 1
            summary[f"fn_{fn_change}"] += 1
            src = _detect_source(r)
            source_counts[src] += 1

            w.writerow({
                "source_path": str(path),
                "decision_source": src,
                "kept_folder": path.parent.name,
                "current_filename": current_fn,
                "new_filename": new_fn,
                "filename_changes": fn_change,
                "title_old": current.get("title", ""),
                "title_new": api_title,
                "artist_old": current.get("artist", ""),
                "artist_new": api_artist,
                "album_old": current.get("album", ""),
                "album_new": api_album,
                "year_old": current.get("year", ""),
                "year_new": api_year,
                "track_old": current.get("track", ""),
                "track_new": track_num,
                "albumartist_old": current.get("albumartist", ""),
                "albumartist_new": api_artist,
                "genre_old": current.get("genre", ""),
                "genre_new": api_genre,
                "n_field_changes": n_changes,
            })

    return {
        "total": len(rows),
        "promoted": promoted,
        "eligible": len(eligible),
        "field_actions": dict(summary),
        "by_source": dict(source_counts),
        "diff_csv": str(dryrun_csv_out),
    }


def pick_source_for_row(
    row: dict,
    preference: list[str],
) -> tuple[dict | None, str]:
    """Return (fields_dict, source_label) following the preference order.

    Each tier pulls from its own CSV column prefix: ``jio_*`` for JioSaavn,
    ``shazam_*`` for Shazam, ``api_*`` for iTunes. JioSaavn doesn't carry a
    track number; iTunes does. Genre for JioSaavn maps Hindi/Bhojpuri to
    ``Bollywood`` (matches the curated genre we'd want in tags).

    Returns ``(None, "")`` if no tier in ``preference`` has data.
    """
    for tier in preference:
        fields = _extract_tier(row, tier)
        if fields is not None:
            return fields, tier
    return None, ""


def _extract_tier(row: dict, tier: str) -> dict | None:
    if tier == "jiosaavn":
        title = (row.get("jio_title") or "").strip()
        if not title:
            return None
        lang = (row.get("jio_language") or "").lower()
        genre = "Bollywood" if lang in ("hindi", "bhojpuri") else (row.get("jio_language") or "").title()
        return {
            "title": title,
            "artist": (row.get("jio_artist") or "").strip(),
            "album": (row.get("jio_album") or "").strip(),
            "year": (row.get("jio_year") or "").strip()[:4],
            "track": "",
            "genre": genre,
        }
    if tier == "shazam":
        title = (row.get("shazam_title") or "").strip()
        if not title:
            return None
        return {
            "title": title,
            "artist": (row.get("shazam_artist") or "").strip(),
            "album": (row.get("shazam_album") or "").strip(),
            "year": (row.get("shazam_year") or "").strip()[:4],
            "track": "",
            "genre": (row.get("shazam_genre") or "").strip(),
        }
    if tier == "itunes":
        title = (row.get("api_title") or "").strip()
        if not title:
            return None
        return {
            "title": title,
            "artist": (row.get("api_artist") or "").strip(),
            "album": (row.get("api_album") or "").strip(),
            "year": (row.get("api_year") or "").strip()[:4],
            "track": (row.get("api_track_num") or "").strip(),
            "genre": (row.get("api_genre") or "").strip(),
        }
    return None


def _current_track_from_snap(snap: dict) -> str:
    tags_dict = snap.get("tags") or {}
    trck = tags_dict.get("TRCK", "") or tags_dict.get("tracknumber", "") or tags_dict.get("trkn", "")
    if isinstance(trck, list):
        trck = trck[0] if trck else ""
    if isinstance(trck, (list, tuple)):
        trck = "/".join(str(x) for x in trck)
    return str(trck).split("/")[0]


def _apply_guardrail(
    fields: dict,
    snap: dict,
    parent_name: str,
    filename: str,
) -> tuple[dict, bool]:
    """Return (possibly-rewritten fields, guarded?).

    Guardrail: if folder has ``(YYYY)`` AND |folder_year - api_year| >= 3,
    keep folder year + current track number. Catches iTunes-returns-
    compilation cases where the original year would be silently overwritten.
    """
    fy = folder_year(parent_name)
    year = (fields.get("year") or "").strip()
    if not (fy and year.isdigit() and abs(int(year) - fy) >= 3):
        return fields, False

    current_trck = _current_track_from_snap(snap)
    if current_trck and current_trck.isdigit() and 1 <= int(current_trck) <= 99:
        guard_track = str(int(current_trck))
    else:
        m = TRACK_PREFIX_RX.match(filename or "")
        guard_track = str(int(m.group(1))) if (m and 1 <= int(m.group(1)) <= 99) else ""

    out = dict(fields)
    out["year"] = str(fy)
    out["track"] = guard_track
    return out, True


def _resolve_track_num(api_track: str, snap: dict, filename: str) -> str:
    return derive_track_num(api_track, _current_track_from_snap(snap), filename)


def _row_lookup(merged_csv: Path) -> dict[str, dict]:
    with Path(merged_csv).open("r", encoding="utf-8") as f:
        return {r.get("source_path", ""): r for r in csv.DictReader(f)}


def _persist_state(
    snapshot: dict,
    undo_ops: list[dict],
    snapshot_path: Path,
    undo_script_path: Path,
) -> None:
    """Write the snapshot JSON + the thin undo script (with sibling ops)."""
    snapshot_path = Path(snapshot_path)
    undo_script_path = Path(undo_script_path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    backup.generate_undo_script(snapshot_path, undo_ops, undo_script_path)


def _rename_with_collision(
    path: Path,
    new_name_base: str,
    ext: str,
) -> tuple[Path, bool]:
    """Rename ``path`` to ``<new_name_base><ext>`` in the same folder.

    Adds ``(2)``, ``(3)``... suffix on collision. Returns (final_target,
    collided?). If the computed target equals ``path``, returns (path, False)
    without renaming.
    """
    target = path.parent / (safe_filename(new_name_base) + ext)
    if target == path:
        return path, False
    collided = False
    i = 2
    while target.exists() and target != path:
        target = path.parent / (safe_filename(f"{new_name_base} ({i})") + ext)
        i += 1
        collided = True
    path.rename(target)
    return target, collided


def apply(
    diff_csv: Path,
    merged_csv: Path,
    snapshot_path: Path,
    undo_script_path: Path,
    *,
    dry_run: bool = False,
    rename: bool = True,
    include_low: bool = False,
    source_preference: list[str] | None = None,
) -> ApplyResult:
    """Walk the diff CSV (or merged CSV for ``include_low``) and apply edits.

    For ``auto_apply`` rows we use the already-merged ``api_*`` columns
    (which is whichever tier scored highest in 16_shazam). For
    ``include_low`` we walk the merged CSV and pick the first source from
    ``source_preference`` that has data; rows where no tier has data are
    skipped with reason ``no_source``.

    The snapshot is captured BEFORE any write, and the undo state is
    re-persisted after each successful row so a crash mid-run still
    yields a valid undo script for everything written so far.

    ``dry_run=True`` skips writes/renames but still computes snapshots and
    op records, useful for diff verification without filesystem mutation.
    """
    diff_csv = Path(diff_csv)
    merged_csv = Path(merged_csv)
    snapshot_path = Path(snapshot_path)
    undo_script_path = Path(undo_script_path)
    preference = source_preference or _DEFAULT_SOURCE_PREFERENCE

    if include_low:
        with merged_csv.open("r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        merged_by_path: dict[str, dict] = {r.get("source_path", ""): r for r in rows}
    else:
        with diff_csv.open("r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        merged_by_path = _row_lookup(merged_csv) if merged_csv.exists() else {}

    snapshot: dict[str, dict] = {}
    undo_ops: list[dict] = []
    result = ApplyResult()

    for r in rows:
        source_path = r.get("source_path", "")
        path = Path(source_path)
        if not source_path or not path.exists():
            result.errors += 1
            continue

        if include_low:
            merged_row = r
            picked, _src_label = pick_source_for_row(merged_row, preference)
            if picked is None:
                continue
            api_title = picked["title"]
            api_artist = picked["artist"]
            api_album = picked["album"]
            api_year = picked["year"]
            api_track = picked["track"]
            api_genre = picked["genre"]
        else:
            merged_row = merged_by_path.get(source_path, r)
            api_title = (merged_row.get("api_title") or "").strip()
            api_artist = (merged_row.get("api_artist") or "").strip()
            api_album = (merged_row.get("api_album") or "").strip()
            api_year = (merged_row.get("api_year") or "").strip()[:4]
            api_track = (merged_row.get("api_track_num") or "").strip()
            api_genre = (merged_row.get("api_genre") or "").strip()

        if not api_title:
            continue

        result.files_touched += 1

        snap = tags_mod.snapshot(path)
        snapshot[str(path)] = snap

        track_num = _resolve_track_num(api_track, snap, path.name)
        candidate = {
            "title": api_title,
            "artist": api_artist,
            "albumartist": api_artist,
            "album": api_album,
            "year": api_year,
            "track": track_num,
            "genre": api_genre,
        }
        guarded_fields, was_guarded = _apply_guardrail(candidate, snap, path.parent.name, path.name)
        if was_guarded:
            result.guarded += 1

        ext = path.suffix.lower()
        if ext not in _WRITABLE_EXTS:
            # Skip silently — unsupported extensions weren't in the
            # 18/20 pipeline either. Don't count as error.
            undo_ops.append({"original_path": str(path), "new_path": str(path)})
            _persist_state(snapshot, undo_ops, snapshot_path, undo_script_path)
            continue

        if not dry_run:
            try:
                tags_mod.write(path, guarded_fields)
                result.tags_written += 1
            except Exception:
                result.errors += 1
                undo_ops.append({"original_path": str(path), "new_path": str(path)})
                _persist_state(snapshot, undo_ops, snapshot_path, undo_script_path)
                continue
        else:
            result.tags_written += 1

        if rename:
            track_for_name = guarded_fields.get("track", "")
            if track_for_name and track_for_name.isdigit() and 1 <= int(track_for_name) <= 99:
                new_name_base = f"{int(track_for_name):02d} - {api_title}"
            else:
                new_name_base = api_title
            if dry_run:
                undo_ops.append({"original_path": str(path), "new_path": str(path)})
            else:
                try:
                    target, collided = _rename_with_collision(path, new_name_base, ext)
                    if collided:
                        result.collisions += 1
                    if target != path:
                        result.renamed += 1
                    undo_ops.append({"original_path": str(path), "new_path": str(target)})
                except Exception:
                    result.errors += 1
                    undo_ops.append({"original_path": str(path), "new_path": str(path)})
        else:
            undo_ops.append({"original_path": str(path), "new_path": str(path)})

        _persist_state(snapshot, undo_ops, snapshot_path, undo_script_path)

    # Always write final state, even if zero rows processed.
    _persist_state(snapshot, undo_ops, snapshot_path, undo_script_path)

    result.snapshot_path = snapshot_path
    result.undo_script_path = undo_script_path
    return result


def apply_approvals(
    merged_csv: Path,
    approvals_json: Path,
    snapshot_path: Path,
    undo_script_path: Path,
    *,
    dry_run: bool = False,
    rename: bool = True,
) -> ApplyResult:
    """Apply user approvals from ``19_approvals.json``.

    Each approval entry: ``{source_path, pick, manual_fields}`` where ``pick``
    is one of ``itunes``, ``jiosaavn``, ``shazam``, ``manual``, ``skip``.
    ``manual_fields`` (only meaningful when pick=manual) must carry at least
    ``title``; missing optional fields fall back to the row's current tags
    (artist/album/year) so a manual title correction doesn't blank metadata.
    """
    merged_csv = Path(merged_csv)
    approvals_json = Path(approvals_json)
    snapshot_path = Path(snapshot_path)
    undo_script_path = Path(undo_script_path)

    approvals = json.loads(approvals_json.read_text(encoding="utf-8"))
    by_path: dict[str, dict] = {a["source_path"]: a for a in approvals if a.get("source_path")}

    with merged_csv.open("r", encoding="utf-8") as f:
        merged_rows = {r.get("source_path", ""): r for r in csv.DictReader(f)}

    snapshot: dict[str, dict] = {}
    undo_ops: list[dict] = []
    result = ApplyResult()

    for source_path, approval in by_path.items():
        pick = (approval.get("pick") or "").lower()
        if pick in ("", "skip"):
            continue

        path = Path(source_path)
        if not path.exists():
            result.errors += 1
            continue

        row = merged_rows.get(source_path) or {}

        if pick == "manual":
            manual = approval.get("manual_fields") or {}
            title = (manual.get("title") or "").strip()
            if not title:
                result.errors += 1
                continue
            fields = {
                "title": title,
                "artist": (manual.get("artist") or row.get("cur_artist") or row.get("api_artist") or "").strip(),
                "album": (manual.get("album") or row.get("cur_album") or row.get("api_album") or "").strip(),
                "year": ((manual.get("year") or row.get("cur_year") or row.get("api_year") or "").strip())[:4],
                "track": (manual.get("track") or "").strip(),
                "genre": (manual.get("genre") or row.get("api_genre") or "").strip(),
            }
        else:
            picked, _ = pick_source_for_row(row, [pick])
            if picked is None:
                result.errors += 1
                continue
            fields = picked

        result.files_touched += 1

        snap = tags_mod.snapshot(path)
        snapshot[str(path)] = snap

        track_num = _resolve_track_num(fields.get("track", ""), snap, path.name)
        candidate = {
            "title": fields["title"],
            "artist": fields["artist"],
            "albumartist": fields["artist"],
            "album": fields["album"],
            "year": fields["year"],
            "track": track_num,
            "genre": fields["genre"],
        }
        guarded_fields, was_guarded = _apply_guardrail(candidate, snap, path.parent.name, path.name)
        if was_guarded:
            result.guarded += 1

        ext = path.suffix.lower()
        if ext not in _WRITABLE_EXTS:
            undo_ops.append({"original_path": str(path), "new_path": str(path)})
            _persist_state(snapshot, undo_ops, snapshot_path, undo_script_path)
            continue

        if not dry_run:
            try:
                tags_mod.write(path, guarded_fields)
                result.tags_written += 1
            except Exception:
                result.errors += 1
                undo_ops.append({"original_path": str(path), "new_path": str(path)})
                _persist_state(snapshot, undo_ops, snapshot_path, undo_script_path)
                continue
        else:
            result.tags_written += 1

        if rename:
            track_for_name = guarded_fields.get("track", "")
            if track_for_name and track_for_name.isdigit() and 1 <= int(track_for_name) <= 99:
                new_name_base = f"{int(track_for_name):02d} - {fields['title']}"
            else:
                new_name_base = fields["title"]
            if dry_run:
                undo_ops.append({"original_path": str(path), "new_path": str(path)})
            else:
                try:
                    target, collided = _rename_with_collision(path, new_name_base, ext)
                    if collided:
                        result.collisions += 1
                    if target != path:
                        result.renamed += 1
                    undo_ops.append({"original_path": str(path), "new_path": str(target)})
                except Exception:
                    result.errors += 1
                    undo_ops.append({"original_path": str(path), "new_path": str(path)})
        else:
            undo_ops.append({"original_path": str(path), "new_path": str(path)})

        _persist_state(snapshot, undo_ops, snapshot_path, undo_script_path)

    _persist_state(snapshot, undo_ops, snapshot_path, undo_script_path)

    result.snapshot_path = snapshot_path
    result.undo_script_path = undo_script_path
    return result
