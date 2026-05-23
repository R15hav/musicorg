"""Discover and summarize libraries already known to musicorg.

The library's state directory layout is documented in
[README.md](../../README.md); we walk ``state_root()`` and read each
slug's ``config.ini`` + on-disk markers to produce one ``KnownLibrary``
record per discovered slug. Used by the Welcome screen's recent-
libraries list.

No library API hidden behind this — just file reads.
"""

from __future__ import annotations

import configparser
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class KnownLibrary:
    slug: str
    root: Path
    state_dir: Path
    track_count: int
    organized: bool  # an undo_*.sh exists → execute_plan has run
    last_modified: datetime
    apply_mode: str
    default_country: str

    @property
    def display_subtitle(self) -> str:
        bits: list[str] = []
        if self.track_count:
            bits.append(f"{self.track_count} track{'s' if self.track_count != 1 else ''}")
        bits.append(self.last_modified.strftime("%Y-%m-%d %H:%M"))
        return f"{self.root}  ·  " + "  ·  ".join(bits)

    @property
    def status_label(self) -> str:
        return "Organized" if self.organized else "Partial"


def list_known_libraries(state_root: Path) -> list[KnownLibrary]:
    """Return every library slug under ``state_root`` with a valid config.ini.

    Sorted newest-first by state-directory mtime. Slug directories
    without a config.ini, or with an unreadable config, are skipped.
    """
    if not state_root.exists() or not state_root.is_dir():
        return []

    out: list[KnownLibrary] = []
    for child in state_root.iterdir():
        if not child.is_dir():
            continue
        cfg_ini = child / "config.ini"
        if not cfg_ini.exists():
            continue
        parser = configparser.ConfigParser()
        try:
            parser.read(cfg_ini)
        except configparser.Error:
            continue
        if not parser.has_section("library"):
            continue
        root_str = parser.get("library", "root", fallback="")
        if not root_str:
            continue
        root = Path(root_str)

        track_count = 0
        tags_csv = child / "01_tags.csv"
        if tags_csv.exists():
            try:
                with tags_csv.open() as fh:
                    track_count = max(sum(1 for _ in fh) - 1, 0)
            except OSError:
                track_count = 0

        organized = any(child.glob("undo_*.sh"))

        try:
            mtime = datetime.fromtimestamp(child.stat().st_mtime)
        except OSError:
            mtime = datetime.fromtimestamp(0)

        apply_mode = ""
        country = ""
        if parser.has_section("defaults"):
            apply_mode = parser.get("defaults", "apply_mode", fallback="")
            country = parser.get("defaults", "country", fallback="")

        out.append(KnownLibrary(
            slug=child.name,
            root=root,
            state_dir=child,
            track_count=track_count,
            organized=organized,
            last_modified=mtime,
            apply_mode=apply_mode or "move",
            default_country=country or "bollywood",
        ))

    out.sort(key=lambda k: k.last_modified, reverse=True)
    return out


def delete_library_state(library: KnownLibrary) -> None:
    """Remove a library's state directory. Destructive — caller confirms."""
    shutil.rmtree(library.state_dir, ignore_errors=False)
