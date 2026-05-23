"""JioSaavn search API lookup tier.

Second tier — best free Bollywood / Punjabi catalog. JioSaavn returns no
duration in search results, so the scorer drops the duration sub-score
automatically when ``api_duration_sec=0.0`` is passed.
"""

from __future__ import annotations

import html
import json
import time
import urllib.parse
import urllib.request

from ..clean import clean_for_query
from ..models import TierMatch
from .scoring import score_match


JIOSAAVN_URL = "https://www.jiosaavn.com/api.php"
_SLEEP_SEC = 0.6
_TIMEOUT_SEC = 12


def _query(term: str, user_agent: str, limit: int = 5) -> list[dict]:
    params = {
        "__call": "search.getResults",
        "_format": "json",
        "_marker": "0",
        "api_version": "4",
        "ctx": "web6dot0",
        "q": term,
        "n": str(limit),
        "p": "1",
    }
    qs = urllib.parse.urlencode(params)
    url = f"{JIOSAAVN_URL}?{qs}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as r:
            data = json.load(r)
        return data.get("results", []) if isinstance(data, dict) else []
    except Exception:
        return []


def _parse_subtitle(subtitle: str) -> tuple[str, str]:
    """JioSaavn ``subtitle`` is ``"Artist1, Artist2 - Album"``. Split on the
    rightmost `` - `` so multi-artist credits stay together."""
    if not subtitle:
        return "", ""
    s = html.unescape(subtitle).strip()
    if " - " in s:
        artist, album = s.rsplit(" - ", 1)
        return artist.strip(), album.strip()
    return s, ""


def search(
    title: str,
    artist: str,
    album_hint: str,
    folder_year: str,
    user_agent: str = "Mozilla/5.0",
) -> TierMatch | None:
    """Search JioSaavn for ``title`` / ``artist`` and return the best match.

    Returns ``None`` on no results or any network error. Sleeps 0.6s after the
    request — JioSaavn tolerates faster polling than iTunes.
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

    results = _query(term, user_agent=user_agent)
    time.sleep(_SLEEP_SEC)
    if not results:
        return None

    best: tuple[float, dict, dict, str, str] | None = None
    for r in results:
        api_title = html.unescape(r.get("title") or "").strip()
        api_artist, api_album = _parse_subtitle(r.get("subtitle") or "")
        api_year = (r.get("year") or "").strip()[:4]
        s, parts_dbg = score_match(
            api_title=api_title,
            api_artist=api_artist,
            api_album=api_album,
            api_year=api_year,
            api_duration_sec=0.0,
            cur_title=cur_t,
            cur_artist=cur_a,
            cur_duration_sec=0.0,
            album_hint=album_hint,
            folder_year=folder_year,
        )
        if best is None or s > best[0]:
            best = (s, parts_dbg, r, api_title, api_artist)

    if best is None:
        return None
    s, parts_dbg, top, api_title, api_artist = best
    _, api_album = _parse_subtitle(top.get("subtitle") or "")
    language = (top.get("language") or "").strip().lower()
    genre = "Bollywood" if language in ("hindi", "bhojpuri") else language.title()
    return TierMatch(
        tier="jiosaavn",
        confidence=s,
        title=api_title,
        artist=api_artist,
        album=api_album,
        year=(top.get("year") or "").strip()[:4],
        track_num="",
        genre=genre,
        cover_url=html.unescape(top.get("image") or "").strip(),
        apple_music_url="",
        adam_id="",
        storefront="",
        isrc="",
        raw=top,
        score_parts=parts_dbg,
    )
