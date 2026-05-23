"""Read + write audio tags via mutagen, with ffprobe + mediainfo fallback.

Read path covers ~88-90 % of typical libraries via mutagen alone, with the
two CLI tools picking up another 1-2 %. Bitrate normalisation: mutagen
returns bps for some formats and kbps for others — divide by 1000 if > 10k.

Write path: ID3v2.4 frames for MP3, MP4 atoms for M4A, Vorbis Comments for
FLAC. ``delall()`` before writing to avoid duplicate frames.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TPE2, TALB, TDRC, TRCK, TCON
from mutagen.mp4 import MP4

from ._binaries import ffprobe_path, mediainfo_path
from .models import Track


AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".aac", ".ogg", ".opus", ".wma"}
LOSSY_EXTS = {".mp3", ".aac", ".ogg", ".opus", ".wma"}
LOSSLESS_EXTS = {".flac", ".wav", ".m4a"}  # m4a is ambiguous — verify via codec

TAG_ALIASES: dict[str, list[str]] = {
    "title":       ["TIT2", "title", "\xa9nam"],
    "artist":      ["TPE1", "artist", "\xa9ART"],
    "album":       ["TALB", "album", "\xa9alb"],
    "year":        ["TDRC", "TYER", "date", "\xa9day"],
    "track":       ["TRCK", "tracknumber", "trkn"],
    "genre":       ["TCON", "genre", "\xa9gen"],
    "albumartist": ["TPE2", "albumartist", "aART"],
}


def _first_tag(audio: Any, keys: list[str]) -> str:
    if not getattr(audio, "tags", None):
        return ""
    for k in keys:
        v = audio.tags.get(k)
        if v is None:
            continue
        if hasattr(v, "text"):
            v = v.text
        if isinstance(v, (list, tuple)):
            v = v[0] if v else ""
        if isinstance(v, tuple):
            v = "/".join(str(x) for x in v)
        s = str(v).strip()
        if s:
            return s
    return ""


def _from_mutagen(path: Path) -> tuple[dict | None, str, float, int]:
    try:
        audio = MutagenFile(path)
    except Exception as e:
        return None, f"mutagen-error: {e}", 0.0, 0
    if audio is None:
        return None, "mutagen-unsupported", 0.0, 0
    info = getattr(audio, "info", None)
    duration = float(getattr(info, "length", 0.0) or 0.0)
    bitrate = int(getattr(info, "bitrate", 0) or 0)
    if bitrate > 10000:
        bitrate //= 1000
    if not getattr(audio, "tags", None):
        return {}, "mutagen-no-tags", duration, bitrate
    out = {k: _first_tag(audio, aliases) for k, aliases in TAG_ALIASES.items()}
    has_any = any(out.get(k) for k in ("title", "artist", "album"))
    return out, ("mutagen-ok" if has_any else "mutagen-empty"), duration, bitrate


def _from_ffprobe(path: Path) -> tuple[dict | None, str, float, int]:
    try:
        r = subprocess.run(
            [ffprobe_path(), "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return None, f"ffprobe-rc={r.returncode}", 0.0, 0
        data = json.loads(r.stdout)
        fmt = data.get("format") or {}
        tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
        duration = float(fmt.get("duration", 0) or 0)
        bitrate = int(int(fmt.get("bit_rate", 0) or 0) / 1000)
        out = {
            "title":       tags.get("title", ""),
            "artist":      tags.get("artist", ""),
            "album":       tags.get("album", ""),
            "year":        tags.get("date") or tags.get("year") or tags.get("tyer", ""),
            "track":       tags.get("track", ""),
            "genre":       tags.get("genre", ""),
            "albumartist": tags.get("album_artist") or tags.get("albumartist", ""),
        }
        has_any = any(out[k] for k in ("title", "artist", "album"))
        return out, ("ffprobe-ok" if has_any else "ffprobe-empty"), duration, bitrate
    except FileNotFoundError:
        return None, "ffprobe-missing", 0.0, 0
    except Exception as e:
        return None, f"ffprobe-error: {e}", 0.0, 0


def _from_mediainfo(path: Path) -> tuple[dict | None, str, float, int]:
    try:
        r = subprocess.run(
            [mediainfo_path(), "--Output=JSON", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return None, f"mediainfo-rc={r.returncode}", 0.0, 0
        data = json.loads(r.stdout)
        tracks = data.get("media", {}).get("track", [])
        general = next((t for t in tracks if t.get("@type") == "General"), {})
        audio_t = next((t for t in tracks if t.get("@type") == "Audio"), {})
        duration_ms = float(general.get("Duration", 0) or 0)
        duration = duration_ms if duration_ms < 100000 else duration_ms / 1000.0
        bitrate = int(int(float(audio_t.get("BitRate", 0) or 0)) / 1000)
        out = {
            "title":       general.get("Title", ""),
            "artist":      general.get("Performer") or general.get("Artist", ""),
            "album":       general.get("Album", ""),
            "year":        general.get("Recorded_Date") or general.get("Released_Date", ""),
            "track":       general.get("Track_Position", ""),
            "genre":       general.get("Genre", ""),
            "albumartist": general.get("Album_Performer", ""),
        }
        has_any = any(out[k] for k in ("title", "artist", "album"))
        return out, ("mediainfo-ok" if has_any else "mediainfo-empty"), duration, bitrate
    except FileNotFoundError:
        return None, "mediainfo-missing", 0.0, 0
    except Exception as e:
        return None, f"mediainfo-error: {e}", 0.0, 0


def read(path: Path) -> Track:
    """Read audio tags from ``path`` via mutagen, then ffprobe, then mediainfo.

    Each backend is tried in order until one returns useful tags. ``tag_source``
    on the returned :class:`Track` records which backend succeeded (e.g.
    ``"mutagen-ok"``, ``"ffprobe-ok"``). Bitrate is normalised to kbps
    regardless of which backend returned it.
    """
    p = Path(path)
    size = p.stat().st_size if p.exists() else 0
    tags, src, duration, bitrate = _from_mutagen(p)
    if (not tags) or src in ("mutagen-no-tags", "mutagen-empty", "mutagen-unsupported"):
        t2, s2, d2, b2 = _from_ffprobe(p)
        if t2 is not None:
            if s2.endswith("-ok") or not tags:
                tags, src = t2, s2
            duration = duration or d2
            bitrate = bitrate or b2
    if (not tags) or (not any((tags or {}).get(k, "") for k in ("title", "artist", "album"))):
        t3, s3, d3, b3 = _from_mediainfo(p)
        if t3 is not None and s3.endswith("-ok"):
            tags, src = t3, s3
            duration = duration or d3
            bitrate = bitrate or b3
    tags = tags or {}
    return Track(
        path=p,
        size=size,
        bitrate_kbps=int(bitrate or 0),
        duration_sec=round(float(duration or 0), 1),
        tag_source=src,
        title=tags.get("title", ""),
        artist=tags.get("artist", ""),
        album=tags.get("album", ""),
        albumartist=tags.get("albumartist", ""),
        year=tags.get("year", ""),
        track=tags.get("track", ""),
        genre=tags.get("genre", ""),
    )


def snapshot(path: Path) -> dict:
    """Capture every tag frame currently on ``path`` as a JSON-serialisable dict.

    Used before a tag write to enable undo. Returns
    ``{original_filename, ext, tags: {frame_key: value}}``. Any frame that
    cannot be serialised is silently dropped; partial snapshots are still
    valid for undo purposes.
    """
    snap: dict = {"original_filename": path.name, "ext": path.suffix.lower(), "tags": {}}
    try:
        a = MutagenFile(path)
        if a and a.tags:
            for k, v in a.tags.items():
                try:
                    if hasattr(v, "text"):
                        val = v.text
                    elif isinstance(v, (list, tuple)):
                        val = list(v)
                    else:
                        val = v
                    if isinstance(val, (list, tuple)):
                        val = [str(x) for x in val]
                    else:
                        val = str(val)
                    snap["tags"][str(k)] = val
                except Exception:
                    pass
    except Exception:
        pass
    return snap


def write_mp3(path: Path, fields: dict[str, str]) -> None:
    """Write ID3v2.4. Overwrites the seven canonical frames; leaves others."""
    try:
        audio = ID3(path)
    except ID3NoHeaderError:
        audio = ID3()
    for k in ("TIT2", "TPE1", "TPE2", "TALB", "TDRC", "TRCK", "TCON"):
        audio.delall(k)
    if fields.get("title"):       audio.add(TIT2(encoding=3, text=fields["title"]))
    if fields.get("artist"):      audio.add(TPE1(encoding=3, text=fields["artist"]))
    if fields.get("albumartist"): audio.add(TPE2(encoding=3, text=fields["albumartist"]))
    if fields.get("album"):       audio.add(TALB(encoding=3, text=fields["album"]))
    if fields.get("year"):        audio.add(TDRC(encoding=3, text=str(fields["year"])[:4]))
    if fields.get("track"):       audio.add(TRCK(encoding=3, text=str(fields["track"])))
    if fields.get("genre"):       audio.add(TCON(encoding=3, text=fields["genre"]))
    audio.save(path, v2_version=4)


def write_mp4(path: Path, fields: dict[str, str]) -> None:
    audio = MP4(path)
    for k in ("\xa9nam", "\xa9ART", "aART", "\xa9alb", "\xa9day", "\xa9gen", "trkn"):
        if k in audio:
            del audio[k]
    if fields.get("title"):       audio["\xa9nam"] = [fields["title"]]
    if fields.get("artist"):      audio["\xa9ART"] = [fields["artist"]]
    if fields.get("albumartist"): audio["aART"]    = [fields["albumartist"]]
    if fields.get("album"):       audio["\xa9alb"] = [fields["album"]]
    if fields.get("year"):        audio["\xa9day"] = [str(fields["year"])[:4]]
    if fields.get("genre"):       audio["\xa9gen"] = [fields["genre"]]
    if fields.get("track"):
        try:
            audio["trkn"] = [(int(fields["track"]), 0)]
        except Exception:
            pass
    audio.save()


def write_flac(path: Path, fields: dict[str, str]) -> None:
    audio = FLAC(path)
    for k in ("TITLE", "ARTIST", "ALBUMARTIST", "ALBUM", "DATE", "TRACKNUMBER", "GENRE"):
        if k in audio:
            del audio[k]
    if fields.get("title"):       audio["TITLE"]       = fields["title"]
    if fields.get("artist"):      audio["ARTIST"]      = fields["artist"]
    if fields.get("albumartist"): audio["ALBUMARTIST"] = fields["albumartist"]
    if fields.get("album"):       audio["ALBUM"]       = fields["album"]
    if fields.get("year"):        audio["DATE"]        = str(fields["year"])[:4]
    if fields.get("track"):       audio["TRACKNUMBER"] = str(fields["track"])
    if fields.get("genre"):       audio["GENRE"]       = fields["genre"]
    audio.save()


def write(path: Path, fields: dict[str, str]) -> None:
    """Write canonical tag fields to ``path``, dispatching by extension.

    Supported: ``.mp3`` (ID3v2.4), ``.m4a`` (MP4 atoms), ``.flac`` (Vorbis
    Comments). Raises ``ValueError`` for any other extension. The seven
    canonical fields are: title, artist, albumartist, album, year, track, genre.
    """
    ext = path.suffix.lower()
    if ext == ".mp3":
        write_mp3(path, fields)
    elif ext == ".m4a":
        write_mp4(path, fields)
    elif ext == ".flac":
        write_flac(path, fields)
    else:
        raise ValueError(f"unsupported audio extension for tag write: {ext}")
