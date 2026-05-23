"""iTunes Search API lookup tier.

First tier in the cascade: best Hollywood / international coverage, decent
Bollywood, and provides the Apple Music URL + adam_id we need for the hi-res
upgrade pass downstream. Self-contained on ``urllib.request`` to keep the
module dependency-light.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from ..clean import clean_for_query
from ..models import TierMatch
from .scoring import score_match


ITUNES_URL = "https://itunes.apple.com/search"
_SLEEP_SEC = 0.8
_TIMEOUT_SEC = 12


def _query(term: str, country: str, limit: int = 5) -> list[dict]:
    qs = urllib.parse.urlencode(
        {"term": term, "media": "music", "entity": "song", "limit": limit, "country": country}
    )
    url = f"{ITUNES_URL}?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SEC) as r:
            data = json.load(r)
        return data.get("results", []) if isinstance(data, dict) else []
    except Exception:
        return []


def search(
    title: str,
    artist: str,
    album_hint: str,
    folder_year: str,
    duration_sec: float,
    country: str = "us",
) -> TierMatch | None:
    """Search iTunes for ``title`` / ``artist`` and return the best scored match.

    Returns ``None`` if no results or on any network error. The 0.8s sleep
    after the request stays inside this function so callers don't need to
    rate-limit themselves.
    """
    cur_t = clean_for_query(title) or (title or "").strip()
    cur_a = clean_for_query(artist)
    parts = [cur_t]
    if cur_a:
        parts.append(cur_a)
    elif album_hint:
        parts.append(album_hint)
    term = " ".join(p for p in parts if p).strip()[:200]
    if not term:
        return None

    results = _query(term, country=country)
    time.sleep(_SLEEP_SEC)
    if not results:
        return None

    best: tuple[float, dict, dict] | None = None
    for r in results:
        api_title = r.get("trackName") or ""
        api_artist = r.get("artistName") or ""
        api_album = r.get("collectionName") or ""
        api_year = (r.get("releaseDate") or "")[:4]
        api_duration = (r.get("trackTimeMillis") or 0) / 1000.0
        s, parts_dbg = score_match(
            api_title=api_title,
            api_artist=api_artist,
            api_album=api_album,
            api_year=api_year,
            api_duration_sec=api_duration,
            cur_title=cur_t,
            cur_artist=cur_a,
            cur_duration_sec=duration_sec,
            album_hint=album_hint,
            folder_year=folder_year,
        )
        if best is None or s > best[0]:
            best = (s, parts_dbg, r)

    if best is None:
        return None
    s, parts_dbg, top = best
    return TierMatch(
        tier="itunes",
        confidence=s,
        title=top.get("trackName", "") or "",
        artist=top.get("artistName", "") or "",
        album=top.get("collectionName", "") or "",
        year=(top.get("releaseDate") or "")[:4],
        track_num=str(top.get("trackNumber", "") or ""),
        genre=top.get("primaryGenreName", "") or "",
        cover_url=top.get("artworkUrl100", "") or "",
        apple_music_url=top.get("trackViewUrl", "") or "",
        adam_id=str(top.get("trackId", "") or ""),
        storefront=country,
        isrc="",
        raw=top,
        score_parts=parts_dbg,
    )
