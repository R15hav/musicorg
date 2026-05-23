"""Cleaning, junk-pattern stripping, and query preparation.

All site-junk regex lives here. The patterns are APPEND-ONLY by convention.
Three callers share this module: the resolve phase (folder/tag/filename
reconciliation) and the iTunes / JioSaavn / Shazam lookup tiers (query
preparation). Centralising the regex avoids the per-script drift the original
20-script pipeline accumulated.
"""

from __future__ import annotations

import re
from pathlib import Path


SITE_JUNK_PATTERNS: list[str] = [
    r"www\.[a-z0-9._-]+\.(?:com|pk|net|in|cc|info|se|me|org)",
    r"\(www\.[a-z0-9._-]+\.[a-z]{2,4}\)",
    r"\[www\.[a-z0-9._-]+\.[a-z]{2,4}\]",
    r"\[[^\]]*(?:com|net|pk|in|me|info|cc)[^\]]*\]",
    r"\(?\b(?:DjPunjab|DJMaza|Mr-?Jatt|MP3Khan|SongsLover|SongsKing|SongsPk"
    r"|Songs\.pk|DjRaag|FreshMaza|PagalWorld|Pagalworld|VipJaTT|KraZyjaTt"
    r"|wapking|WapClubs|TEH-SONGS|TEH-?SONG|samwep|LeBeWafa|BossMobi|DownloadMing"
    r"|YouTube|MyMp3Song|HungamaWap|Hungama|FazMusic|TEHSONG|MP3Khan\.Co"
    r"|MP3Khan\.Com|MP3Mad|Webmusic|funmaza|WapRex|GopalWap|MP3HunGama|Bhojpuri"
    r"|WapLoft|HD-MP3|RaagFM|ReshamMahal|SongsBee|SongsMaza|InsideTune"
    r"|FullSongs|HitMp3|MzcStudios|HeroMaza|MusikMaza|Music[a-z]*Maza"
    r"|mp3lio|mp3evo|mp3trap)\b\.?[a-z]{0,4}\)?\]?",
    r"\[\s*-\s*Dj[^\]]*\]",
    r"::\s*",
    r"\b\d{2,3}\s*Kbps?\b",
    r"\[\d{2,3}\]",
    r"-?\s*\d{2,3}Kb\b",
    r"~\d{2,3}Kbps?",
    r"%20",
    r"\(Official Single\)",
    r"\bHD\b",
    r"\bFull Song\b",
    r"\[\s*Bhojpuri[^\]]*\]",
    r"\bBhojpuri\s+Version\b",
    r"\b(?:By|by)\.\s*\w+",
    r"\bExtanded\b",
    r"\[Rp\]",
]

# Filename-extension leaks into title tags
EXT_LEAK_RX = re.compile(r"\.(?:mp3|m4a|flac|wav|aac|ogg|opus|wma)\b", re.IGNORECASE)

EXTRA_JUNK = re.compile(
    r"(\b(?:www\.[a-z0-9._-]+|[a-z0-9-]+\.(?:com|pk|net|in|cc|info|me|org))\b"
    r"|\[[^\]]*(?:com|net|pk|in|me|info)[^\]]*\]"
    r"|\[Rp\]|@\s*\S+\.\S+|\bBangaloreLiving\b|\bMp3HunGama\b|\[Songs\.PK\])",
    re.IGNORECASE,
)
UNSAFE_FN_RX = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
FOLDER_RX = re.compile(r"^(.*?)\s*\((\d{4})\)\s*$")
TRACK_PREFIX_RX = re.compile(r"^\s*\(?(\d{1,3})\)?[\s._-]+")
YEAR_SUFFIX_RX = re.compile(r"\s*\(\d{4}\)\s*$")

VERSION_KEYWORDS: tuple[str, ...] = (
    "reprise", "unplugged", "acoustic", "live", "remix", "instrumental",
    "karaoke", "extended", "slow", "fast", "club mix", "radio edit", "lo-fi",
    "lofi", "chill", "duet", "dance mix", "dj mix",
)

VERSION_RX = re.compile(
    r"[\(\[][^)\]]*\b(?:" + "|".join(re.escape(k) for k in VERSION_KEYWORDS) + r")\b[^)\]]*[\)\]]",
    re.IGNORECASE,
)

JUNK_SUBSTR = [
    "downloadming", "djmaza", "songslover", "songsking", "songspk", "songs.pk",
    "pagalworld", "mr-jatt", "djpunjab", "freshmaza", "wapking", "wapclubs",
    "krazyjatt", "vipjatt", "lebewafa", "samwep", "youtube", "[128]", "[160]",
    "[192]", "[256]", "[320]", "kbps", "%20", "mp3khan", "mymp3song",
    "bossmobi", "djraag",
]


def strip_junk(s: str) -> str:
    """Strip site-junk site names, bitrate stamps, etc. Returns trimmed text."""
    if not s:
        return ""
    out = s
    for pat in SITE_JUNK_PATTERNS:
        out = re.sub(pat, "", out, flags=re.IGNORECASE)
    out = re.sub(r"\(\s*[-\s]*\)", "", out)
    out = re.sub(r"\[\s*[-\s]*\]", "", out)
    out = re.sub(r"[-_/.\s]{2,}", " ", out)
    out = re.sub(r"^\s*[-_.\s]+", "", out)
    out = re.sub(r"[-_.\s]+\s*$", "", out)
    return out.strip(" -_.")


def clean_for_query(s: str) -> str:
    """Prepare a string for an external search API.

    Preserves version qualifiers (Reprise, Unplugged, ...) which are distinct
    canonical tracks. Strips other parens, ext leaks, feat clauses, and
    truncates at ';' (Tamil multi-credit pollution).
    """
    s = strip_junk(s)
    s = EXT_LEAK_RX.sub("", s)
    if ";" in s:
        s = s.split(";")[0].strip()
    versions = VERSION_RX.findall(s)
    versions = [re.sub(r"[\[\]]", "", v).strip() for v in versions]
    versions = [v if v.startswith("(") else f"({v.strip('()')})" for v in versions]
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"\[.*?\]", "", s)
    s = re.sub(r"\b(?:feat\.?|ft\.?|featuring)\b.*", "", s, flags=re.IGNORECASE)
    if versions:
        s = s.strip() + " " + " ".join(versions)
    if s.count("(") != s.count(")"):
        s = re.sub(r"[\(\)]", "", s)
    if s.count("[") != s.count("]"):
        s = re.sub(r"[\[\]]", "", s)
    return re.sub(r"\s+", " ", s).strip(" -_.,")


def title_version_marker(s: str) -> str:
    """Return the version keyword found in ``s`` (lowercased), or empty."""
    s = (s or "").lower()
    for kw in VERSION_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", s):
            return kw
    return ""


def parse_folder(folder: str) -> tuple[str, str]:
    """Folder is treated as ``Album (YYYY)``. Returns (album, year)."""
    if not folder:
        return "", ""
    m = FOLDER_RX.match(folder)
    if m:
        return strip_junk(m.group(1)), m.group(2)
    return strip_junk(folder), ""


def normalize_year(y: str) -> str:
    """Pull a 4-digit year out of a free-form date string."""
    if not y:
        return ""
    m = re.search(r"\b(19\d{2}|20\d{2})\b", y)
    return m.group(1) if m else ""


def parse_track_num(filename: str, tag_track: str) -> str:
    """Best-effort track number. Filename prefix only if tag has nothing."""
    if tag_track:
        m = re.match(r"^\s*(\d{1,3})", tag_track)
        if m:
            return m.group(1).lstrip("0") or "0"
    m = TRACK_PREFIX_RX.match(filename)
    if m:
        return m.group(1).lstrip("0") or "0"
    return ""


def title_from_filename(filename: str) -> str:
    name = Path(filename).stem
    name = TRACK_PREFIX_RX.sub("", name)
    return strip_junk(name)


def junkiness(filename: str) -> int:
    """Higher = junkier. Used as a tiebreaker in dedupe."""
    s = filename.lower()
    return sum(1 for j in JUNK_SUBSTR if j in s)


def strip_year_suffix(album: str) -> str:
    return YEAR_SUFFIX_RX.sub("", album).strip()


def normalize_album(album: str) -> str:
    """Collapse OST naming variants so siblings group together."""
    a = album
    a = re.sub(r"\s*\((?:Original Motion Picture Soundtrack|OST|Original Soundtrack)\)\s*", "", a, flags=re.IGNORECASE)
    a = re.sub(r"\s+-\s+(?:OST|Original Soundtrack)\s*$", "", a, flags=re.IGNORECASE)
    a = re.sub(r"\s+(?:OST)\s*$", "", a, flags=re.IGNORECASE)
    return a.strip()


def safe(s: str, maxlen: int = 120) -> str:
    """Filesystem-safe path component."""
    s = (s or "").strip()
    s = EXTRA_JUNK.sub("", s)
    s = UNSAFE_FN_RX.sub("", s)
    s = s.replace("_", " ")
    s = re.sub(r"Volume\.\s*(\d+)", r"Volume \1", s, flags=re.IGNORECASE)
    s = re.sub(r"Vol\.\s*(\d+)", r"Vol \1", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"^[\s_.,;:!@#&-]+", "", s)
    s = re.sub(r"[\s_.,;:!@#&-]+$", "", s)
    if s.count("(") > s.count(")"):
        s = re.sub(r"\([^)]*$", "", s).rstrip()
    if s.count("[") > s.count("]"):
        s = re.sub(r"\[[^\]]*$", "", s).rstrip()
    s = s.strip(" .")
    if s.lower() in {"www", "single track", "unknown artist", "various artists",
                     "delusive", "various", "artist", ""}:
        s = ""
    return s[:maxlen]


def safe_filename(s: str, maxlen: int = 180) -> str:
    s = (s or "").strip()
    s = UNSAFE_FN_RX.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:maxlen]


def normalize_key(s: str) -> str:
    """Lowercase, alpha-only — used for dedupe grouping."""
    return re.sub(r"[^a-z]", "", (s or "").lower())
