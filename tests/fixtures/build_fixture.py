#!/usr/bin/env python3
"""Regenerate the demo music library at tests/fixtures/library-small/.

Each file is a tiny silent MP3 with ID3v2.4 tags. The corpus is hand-picked
to exercise every interesting code path in the musicorg pipeline:

- album_track routing (Jab Tak Hai Jaan, Ek Villain — folders with `(YYYY)`)
- plain-folder demotion to singles (RISHAV/ — no year, not really an album)
- site-junk stripping ([Songs.PK], (www.PagalWorld.com), etc.)
- duplicate detection (Hamdard appears in two folders at different bitrates)
- Punjabi-artist routing (Guru Randhawa → Singles/Punjabi/)
- Hollywood-artist routing (Linkin Park → Hollywood/2010s/)
- garbage filename + missing tags (Track 06.mp3 — no album, no year)
- version variant (Galliyan (Unplugged) — distinct canonical track)
- misc-sweep targets (AlbumArt.jpg, Folder.jpg, desktop.ini)

Run: python3 tests/fixtures/build_fixture.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

from mutagen.id3 import ID3, TALB, TCON, TDRC, TIT2, TPE1, TRCK


# Tiny MPEG1 Layer 3 frame, 32 kbps, 44.1 kHz mono. Repeat to fake duration.
SILENT_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413
SHORT = SILENT_FRAME * 200   # ~5 sec
LONG = SILENT_FRAME * 400    # ~10 sec


def make(rel: str, *, title: str, artist: str, album: str = "",
         year: str = "", track: str = "", genre: str = "",
         duration: str = "short") -> None:
    p = Path(__file__).parent / "library-small" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(SHORT if duration == "short" else LONG)
    tag = ID3()
    if title:  tag.add(TIT2(encoding=3, text=title))
    if artist: tag.add(TPE1(encoding=3, text=artist))
    if album:  tag.add(TALB(encoding=3, text=album))
    if year:   tag.add(TDRC(encoding=3, text=year))
    if track:  tag.add(TRCK(encoding=3, text=track))
    if genre:  tag.add(TCON(encoding=3, text=genre))
    tag.save(p, v2_version=4)


def main() -> None:
    root = Path(__file__).parent / "library-small"
    if root.exists():
        shutil.rmtree(root)

    # ── Bollywood album with year-tagged folder ─────────────────────────────
    make("Jab Tak Hai Jaan (2012)/01 - Challa.mp3",
         title="Challa", artist="Rabbi Shergill", album="Jab Tak Hai Jaan",
         year="2012", track="1", genre="Bollywood")
    make("Jab Tak Hai Jaan (2012)/02 - Saans.mp3",
         title="Saans", artist="Mohit Chauhan", album="Jab Tak Hai Jaan",
         year="2012", track="2", genre="Bollywood")
    make("Jab Tak Hai Jaan (2012)/03 - Heer.mp3",
         title="Heer", artist="Harshdeep Kaur", album="Jab Tak Hai Jaan",
         year="2012", track="3", genre="Bollywood")

    # ── Bollywood album with site-junk filenames + a real duplicate ─────────
    make("Ek Villain (2014)/01-Galliyan-[Songs.PK].mp3",
         title="Galliyan [Songs.PK]", artist="Ankit Tiwari", album="Ek Villain (2014)",
         year="2014", track="1", genre="Bollywood")
    make("Ek Villain (2014)/02 Hamdard.mp3",
         title="Hamdard", artist="Arijit Singh", album="Ek Villain",
         year="2014", track="2", genre="Bollywood", duration="long")
    make("Ek Villain (2014)/(www.PagalWorld.com) - Banjaara.mp3",
         title="Banjaara (www.PagalWorld.com)", artist="Mohammed Irfan",
         album="Ek Villain", year="2014", track="3", genre="Bollywood")

    # ── Plain folder (not an album) — should demote to Singles ──────────────
    make("RISHAV/Galliyan (Unplugged).mp3",
         title="Galliyan (Unplugged)", artist="Shraddha Kapoor", album="",
         year="2014")
    make("RISHAV/05-Hamdard.mp3",
         title="Hamdard", artist="Arijit Singh", album="", duration="long")

    # ── Punjabi single (artist allow-list hint) ─────────────────────────────
    make("Single (2014)/Suit - Guru Randhawa.mp3",
         title="Suit Suit", artist="Guru Randhawa", album="Single",
         year="2014", track="1")
    make("Single (2014)/Patola - Guru Randhawa.mp3",
         title="Patola", artist="Guru Randhawa", album="Single",
         year="2014", track="2")

    # ── Hollywood album (artist allow-list hint) ────────────────────────────
    make("Living Things (2012)/01 - Lost In The Echo.mp3",
         title="Lost In The Echo", artist="Linkin Park", album="Living Things",
         year="2012", track="1", genre="Rock")
    make("Living Things (2012)/02 - In My Remains.mp3",
         title="In My Remains", artist="Linkin Park", album="Living Things",
         year="2012", track="2", genre="Rock")

    # ── Garbage filename + missing tags (forces title-from-filename) ────────
    make("Track 06.mp3", title="", artist="", album="")

    # ── Non-audio top-level junk for misc-sweep ─────────────────────────────
    (root / "AlbumArt_{4F8B}_Large.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    (root / "Folder.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    (root / "desktop.ini").write_text("[.ShellClassInfo]\nIconResource=,0\n")

    files = sorted(root.rglob("*"))
    audio = [f for f in files if f.suffix.lower() == ".mp3"]
    other = [f for f in files if f.is_file() and f.suffix.lower() != ".mp3"]
    print(f"built {root}")
    print(f"  audio:    {len(audio)} files")
    print(f"  non-audio: {len(other)} files")
    print(f"  folders:  {sum(1 for f in files if f.is_dir())}")


if __name__ == "__main__":
    main()
