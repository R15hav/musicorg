"""Confidence scoring for canonical-metadata lookups.

Production-tuned weights and guardrails — the sparse-input cap, version-mismatch
penalty, and album-strong year-waiver were each added in response to specific
false-positive clusters seen in real libraries. Touch the constants here only
when you can point to an offending case.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from ..clean import title_version_marker


def _album_norm(s: str) -> str:
    s = re.sub(r"\b(19|20)\d{2}\b", "", (s or "").lower())
    s = re.sub(r"\(.*?\)|\[.*?\]", "", s)
    s = re.sub(
        r"\b(original motion picture soundtrack|ost|original soundtrack|soundtrack|deluxe.*$|deluxe edition.*$)\b",
        "",
        s,
    )
    return re.sub(r"[^a-z0-9]", "", s)


def score_match(
    api_title: str,
    api_artist: str,
    api_album: str,
    api_year: str,
    api_duration_sec: float,
    cur_title: str,
    cur_artist: str,
    cur_duration_sec: float,
    album_hint: str,
    folder_year: str,
) -> tuple[float, dict]:
    """Score an API result against the current tags + folder context.

    Returns ``(overall_in_0_to_1, parts_dict)``. ``parts_dict`` exposes the
    weighted sub-scores and any guardrail adjustments so callers can persist
    them for review CSVs and audits.
    """
    t = SequenceMatcher(None, (cur_title or "").lower(), (api_title or "").lower()).ratio()
    a = (
        SequenceMatcher(None, (cur_artist or "").lower(), (api_artist or "").lower()).ratio()
        if cur_artist
        else 0.0
    )
    d = (
        max(0.0, 1.0 - abs(api_duration_sec - cur_duration_sec) / 10.0)
        if (cur_duration_sec and api_duration_sec)
        else 0.0
    )

    if cur_artist and cur_duration_sec and api_duration_sec:
        overall = 0.55 * t + 0.25 * a + 0.20 * d
    elif cur_artist:
        overall = 0.70 * t + 0.30 * a
    elif cur_duration_sec and api_duration_sec:
        overall = 0.70 * t + 0.30 * d
    else:
        overall = t

    album_bonus = 0.0
    album_strong = False
    if album_hint:
        coll_n = _album_norm(api_album)
        hint_n = _album_norm(album_hint)
        if hint_n and coll_n and (
            hint_n in coll_n
            or coll_n in hint_n
            or coll_n.startswith(hint_n[: max(8, len(hint_n) * 2 // 3)])
        ):
            album_bonus = 0.15
            album_strong = True
            overall += album_bonus

    year_penalty = 0.0
    res_year = (api_year or "")[:4]
    if (
        folder_year
        and res_year
        and folder_year.isdigit()
        and res_year.isdigit()
        and not album_strong
    ):
        gap = abs(int(folder_year) - int(res_year))
        if gap > 2:
            year_penalty = min(0.30, gap * 0.05)
            overall -= year_penalty

    version_penalty = 0.0
    om = title_version_marker(cur_title)
    tm = title_version_marker(api_title)
    if om != tm:
        version_penalty = 0.35
        overall -= version_penalty

    sparse_cap = 0.0
    if not cur_artist and not cur_duration_sec and not album_hint and overall > 0.85:
        sparse_cap = overall - 0.85
        overall = 0.85

    overall = max(0.0, min(1.0, overall))
    return round(overall, 3), {
        "title_score": round(t, 3),
        "artist_score": round(a, 3),
        "duration_score": round(d, 3),
        "album_bonus": album_bonus,
        "year_penalty": year_penalty,
        "version_penalty": version_penalty,
        "sparse_cap": sparse_cap,
    }


def decide(score: float, threshold_auto: float = 0.86, threshold_review: float = 0.65) -> str:
    """Bucket a score into ``auto_apply`` / ``review`` / ``low``."""
    if score >= threshold_auto:
        return "auto_apply"
    if score >= threshold_review:
        return "review"
    return "low"
