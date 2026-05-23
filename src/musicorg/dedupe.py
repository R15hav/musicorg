"""Duplicate detection and winner selection over the scan CSV.

A duplicate group keys on normalized ``title|artist`` plus a 3-second
duration bucket; the highest-quality member wins (bitrate, then size,
then metadata completeness, then filename junkiness). Losers are routed
to a quarantine list rather than deleted.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from .clean import junkiness, normalize_key


COMPLETENESS_FIELDS: tuple[str, ...] = (
    "title", "artist", "album", "albumartist", "year", "track", "genre",
)


def completeness(row: dict) -> int:
    """Count non-empty canonical tag fields on a CSV row."""
    return sum(1 for f in COMPLETENESS_FIELDS if (row.get(f) or "").strip())


def score(row: dict) -> tuple:
    """Higher tuple wins. Lossless ext gets +5000 kbps virtual bonus."""
    bitrate = int(row.get("bitrate_kbps") or 0)
    size = int(row.get("size_bytes") or 0)
    ext = Path(row.get("filename") or "").suffix.lower()
    lossless_bonus = 5000 if ext in (".flac", ".wav") else 0
    return (
        bitrate + lossless_bonus,
        size,
        completeness(row),
        -junkiness(row.get("filename") or ""),
    )


def read_tags_csv(path: Path) -> list[dict]:
    """Load the row-per-file tags CSV produced by :func:`musicorg.scan.write_tags_csv`."""
    with Path(path).open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def group_duplicates(
    tracks_csv_rows: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (winners, losers, group_summaries).

    Files with no title and no artist become singleton winners — we can't
    safely cluster on filename alone since the same name can mean different
    things in different folders.
    """
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    singletons: list[dict] = []

    for r in tracks_csv_rows:
        title = (r.get("title") or "").strip()
        artist = (r.get("artist") or "").strip()
        if not title and not artist:
            singletons.append(r)
            continue
        nkey = normalize_key(title) + "|" + normalize_key(artist)
        if not nkey.strip("|"):
            singletons.append(r)
            continue
        try:
            dur = float(r.get("duration_sec") or 0)
        except ValueError:
            dur = 0.0
        dbkt = round(dur / 3) * 3 if dur > 30 else -1
        groups[(nkey, dbkt)].append(r)

    winners: list[dict] = []
    losers: list[dict] = []
    group_summaries: list[dict] = []

    for r in singletons:
        winners.append({**r, "dup_group": "", "dup_role": "singleton"})

    for (nkey, dbkt), members in groups.items():
        if len(members) == 1:
            winners.append({**members[0], "dup_group": "", "dup_role": "singleton"})
            continue
        members_scored = sorted(members, key=score, reverse=True)
        winner = members_scored[0]
        gid = f"G{len(group_summaries) + 1:04d}"
        winners.append({**winner, "dup_group": gid, "dup_role": "winner"})
        for loser in members_scored[1:]:
            losers.append({
                **loser,
                "dup_group": gid,
                "dup_role": "loser",
                "winner_path": winner.get("path", ""),
            })
        group_summaries.append({
            "group": gid,
            "key": nkey,
            "duration_bucket": dbkt,
            "count": len(members),
            "winner": winner.get("path", ""),
            "winner_bitrate": winner.get("bitrate_kbps", ""),
            "winner_completeness": completeness(winner),
            "losers": " | ".join(m.get("path", "") for m in members_scored[1:]),
        })

    return winners, losers, group_summaries


def _input_fieldnames(rows: list[dict]) -> list[str]:
    if not rows:
        return list(COMPLETENESS_FIELDS)
    return list(rows[0].keys())


def write_dedupe_outputs(
    winners: list[dict],
    losers: list[dict],
    groups: list[dict],
    state_dir: Path,
    source_rows: list[dict] | None = None,
) -> dict[str, Path]:
    """Write ``07_winners.csv``, ``07_duplicates.csv``, ``07_groups.csv``.

    Returns a mapping of label → written path.
    """
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    base_fields = _input_fieldnames(source_rows or winners or losers)
    win_path = state_dir / "07_winners.csv"
    dup_path = state_dir / "07_duplicates.csv"
    grp_path = state_dir / "07_groups.csv"

    win_fields = base_fields + ["dup_group", "dup_role"]
    with win_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=win_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(winners)

    dup_fields = base_fields + ["dup_group", "dup_role", "winner_path"]
    with dup_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=dup_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(losers)

    grp_fields = (
        list(groups[0].keys()) if groups else
        ["group", "key", "duration_bucket", "count",
         "winner", "winner_bitrate", "winner_completeness", "losers"]
    )
    with grp_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=grp_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(groups)

    return {"winners": win_path, "duplicates": dup_path, "groups": grp_path}
