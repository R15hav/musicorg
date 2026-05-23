"""Inspect top-level archives and diff their contents against the organized tree.

Real libraries often contain old ``.zip`` bundles whose contents have
already been extracted and organized. We list each archive, normalise the
contained filenames, and compare against the set of organized filenames.
A 100 % match marks the archive as a confirmed duplicate (optionally
quarantined to ``<library>/Music/_duplicates/<name>.zip``).
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from .clean import normalize_key
from .tags import AUDIO_EXTS


def _audio_names_from_zip(zf: zipfile.ZipFile) -> list[str]:
    """Return basenames of audio entries (skips directories)."""
    out: list[str] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = Path(info.filename).name
        if not name:
            continue
        if Path(name).suffix.lower() not in AUDIO_EXTS:
            continue
        out.append(name)
    return out


def _norm_name(name: str) -> str:
    """Normalize a filename for set comparison (key + extension preserved)."""
    stem = Path(name).stem
    ext = Path(name).suffix.lower()
    return normalize_key(stem) + ext


def probe_zips(
    root: Path,
    music_root: Path,
    organized_filenames: set[str],
    quarantine_duplicates: bool = False,
) -> list[dict]:
    """Probe every ``.zip`` directly under ``root``.

    ``organized_filenames`` is the set of basenames already present in the
    organized tree (the caller is responsible for collecting them). Returns
    one report per archive::

        {"zip", "audio_entries", "matched", "unmatched", "match_ratio",
         "is_duplicate", "quarantined_to" (optional), "error" (optional)}
    """
    root = Path(root)
    music_root = Path(music_root)
    dup_root = music_root / "Music" / "_duplicates"

    organized_norm = {_norm_name(n) for n in organized_filenames}

    reports: list[dict] = []
    for p in sorted(root.glob("*.zip")):
        report: dict = {"zip": str(p)}
        try:
            with zipfile.ZipFile(p) as zf:
                entries = _audio_names_from_zip(zf)
        except (zipfile.BadZipFile, OSError) as e:
            report["error"] = f"{type(e).__name__}: {e}"
            report["audio_entries"] = 0
            report["matched"] = 0
            report["unmatched"] = 0
            report["match_ratio"] = 0.0
            report["is_duplicate"] = False
            reports.append(report)
            continue

        norm_entries = [_norm_name(n) for n in entries]
        matched = sum(1 for n in norm_entries if n in organized_norm)
        total = len(norm_entries)
        ratio = (matched / total) if total else 0.0
        is_dup = total > 0 and matched == total

        report.update({
            "audio_entries": total,
            "matched": matched,
            "unmatched": total - matched,
            "match_ratio": round(ratio, 4),
            "is_duplicate": is_dup,
        })

        if is_dup and quarantine_duplicates:
            dst = dup_root / p.name
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
            report["quarantined_to"] = str(dst)

        reports.append(report)

    return reports
