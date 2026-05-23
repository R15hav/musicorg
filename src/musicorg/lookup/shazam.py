"""Shazam audio-fingerprint lookup tier.

Third tier — last resort for files where title + artist text are too broken
for any text-based search to succeed. Talks to Shazam's *internal* API via
shazamio, which can break without warning; the caller is expected to wrap
this module with a CircuitBreaker.
"""

from __future__ import annotations

import asyncio
import re
from difflib import SequenceMatcher
from pathlib import Path

from ..clean import title_version_marker
from ..models import TierMatch


def _best_image_url(track_or_detail: dict) -> str:
    """Pick the largest cover-art URL Shazam offers, preferring HQ."""
    imgs = track_or_detail.get("images") or {}
    for k in ("coverarthq", "coverart", "default", "background"):
        if imgs.get(k):
            return str(imgs[k])
    return ""


def _section_metadata(sections: list) -> dict:
    out = {"album": "", "label": "", "released": ""}
    if not sections:
        return out
    for sec in sections:
        if sec.get("type") == "SONG":
            for item in sec.get("metadata", []) or []:
                t = (item.get("title") or "").lower()
                if t == "album":
                    out["album"] = item.get("text", "")
                elif t == "label":
                    out["label"] = item.get("text", "")
                elif t in ("released", "release date", "released on"):
                    out["released"] = item.get("text", "")
            break
    return out


def extract_apple_music_url(track_or_hub: dict) -> tuple[str, str, str]:
    """Walk Shazam's hub recursively and return the best Apple Music URL.

    Prefers song-level URLs (those carrying ``?i=<adam_id>``) over album-level.
    Normalises ``intent://...`` Android deep-links to ``https://...`` and
    strips the trailing ``#Intent;...`` suffix. Drops ``/subscribe`` URLs.
    Returns ``(url, adam_id, storefront)`` or ``("", "", "")``.
    """
    # Accept either the full track dict (with .hub) or a hub dict directly.
    hub = track_or_hub.get("hub") if isinstance(track_or_hub, dict) and "hub" in track_or_hub else track_or_hub
    if not isinstance(hub, dict):
        return "", "", ""

    candidates: list[str] = []

    def collect(node: object) -> None:
        if isinstance(node, dict):
            uri = node.get("uri") or ""
            if isinstance(uri, str) and uri:
                candidates.append(uri)
            for v in node.values():
                collect(v)
        elif isinstance(node, list):
            for v in node:
                collect(v)

    collect(hub)

    normalised: list[str] = []
    for raw in candidates:
        u = raw
        if u.startswith("intent://"):
            u = "https://" + u[len("intent://"):]
            u = u.split("#Intent;")[0]
        if "/subscribe" in u:
            continue
        if "music.apple.com" not in u:
            continue
        normalised.append(u)

    if not normalised:
        return "", "", ""

    song_level = [u for u in normalised if re.search(r"[?&]i=\d+", u)]
    chosen = song_level[0] if song_level else normalised[0]
    m_adam = re.search(r"[?&]i=(\d+)", chosen)
    adam_id = m_adam.group(1) if m_adam else ""
    m_sf = re.match(r"https?://music\.apple\.com/([a-z]{2})/", chosen)
    storefront = m_sf.group(1) if m_sf else "us"
    return chosen, adam_id, storefront


def score_shazam(api_title: str, api_artist: str, cur_title: str, cur_artist: str) -> float:
    """Score Shazam's hit against current tags.

    Title-only when no current artist; otherwise 70/30 title/artist. Subtract
    a 0.35 version-marker penalty if (e.g.) one side says "reprise" and the
    other doesn't, then add the +0.10 audio-fingerprint trust bonus. Clamped
    to [0, 1].
    """
    t = SequenceMatcher(None, (cur_title or "").lower(), (api_title or "").lower()).ratio()
    if cur_artist:
        a = SequenceMatcher(None, (cur_artist or "").lower(), (api_artist or "").lower()).ratio()
        overall = 0.7 * t + 0.3 * a
    else:
        overall = t
    if title_version_marker(cur_title) != title_version_marker(api_title):
        overall -= 0.35
    overall += 0.10
    return round(max(0.0, min(1.0, overall)), 3)


async def recognize_async(client: object, path: Path, enrich: bool = True) -> TierMatch | None:
    """Recognise ``path`` via a shazamio ``Shazam`` client and assemble a TierMatch.

    Returns ``None`` on a miss. Raises on hard API failure — the caller's
    circuit breaker is responsible for taking action. The returned TierMatch
    has ``confidence=0.0``; scoring is the caller's job (see ``score_shazam``).
    """
    out = await client.recognize(str(path))  # type: ignore[attr-defined]
    track = out.get("track") if isinstance(out, dict) else None
    if not track:
        return None
    key = str(track.get("key", "") or "")
    title = track.get("title", "") or ""
    subtitle = track.get("subtitle", "") or ""
    genre = (track.get("genres") or {}).get("primary", "") or ""
    image = _best_image_url(track)
    apple_url, adam_id, storefront = extract_apple_music_url(track)

    album = ""
    year = ""
    label = ""
    isrc = ""
    if enrich and key:
        try:
            detail = await client.track_about(int(key))  # type: ignore[attr-defined]
            sec = _section_metadata(detail.get("sections", []) or [])
            album = sec["album"]
            rd = detail.get("releasedate") or sec["released"] or ""
            m = re.search(r"\b(19\d{2}|20\d{2})\b", str(rd))
            if m:
                year = m.group(1)
            label = sec["label"]
            isrc = detail.get("isrc", "") or ""
            better = _best_image_url(detail)
            if better:
                image = better
            if not apple_url:
                apple_url, adam_id, storefront = extract_apple_music_url(detail)
        except Exception:
            # Non-fatal — keep the basic recognize() result.
            pass

    raw = {
        "track": track,
        "key": key,
        "label": label,
        "url": track.get("url", "") or "",
    }
    return TierMatch(
        tier="shazam",
        confidence=0.0,
        title=title,
        artist=subtitle,
        album=album,
        year=year,
        track_num="",
        genre=genre,
        cover_url=image,
        apple_music_url=apple_url,
        adam_id=adam_id,
        storefront=storefront,
        isrc=isrc,
        raw=raw,
        score_parts={},
    )


def recognize(path: Path) -> TierMatch | None:
    """Synchronous entry point for the CLI. Raises ``ImportError`` if
    shazamio is not installed; network errors propagate so the caller's
    breaker can count them.
    """
    try:
        import shazamio  # type: ignore
    except ImportError as e:
        raise ImportError(
            "shazamio is required for the Shazam tier. Install with: "
            "uv pip install shazamio"
        ) from e
    client = shazamio.Shazam()
    return asyncio.run(recognize_async(client, Path(path)))
