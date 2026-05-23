"""Per-library + global config loader.

Linux-only paths follow XDG conventions:
   Global config:  ~/.config/musicorg/config.ini
   Per-library:    ~/.local/share/musicorg/<slug>/config.ini
   State dir:      ~/.local/share/musicorg/<slug>/
"""

from __future__ import annotations

import configparser
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))

GLOBAL_CONFIG = XDG_CONFIG_HOME / "musicorg" / "config.ini"
STATE_ROOT = XDG_DATA_HOME / "musicorg"


@dataclass
class Config:
    acoustid_app_api_key: str = ""
    musicbrainz_user_agent: str = "musicorg/0.1 (anonymous)"
    musicbrainz_contact: str = ""
    itunes_country: str = "us"
    jiosaavn_user_agent: str = "Mozilla/5.0"
    shazam_breaker_threshold: int = 5
    shazam_inter_request_sleep_sec: float = 1.5
    default_country: Literal["bollywood", "hollywood", "unknown"] = "bollywood"
    apply_mode: Literal["move", "copy", "symlink"] = "move"
    copy_dest: str = ""
    auto_apply_threshold: float = 0.86
    shazam_auto_apply_threshold: float = 0.50
    review_threshold: float = 0.65
    exclude_prefixes: list[str] = field(default_factory=list)
    library_slug: str = ""
    library_root: str = ""
    state_dir: str = ""

    @property
    def state_path(self) -> Path:
        return Path(self.state_dir)

    @property
    def root_path(self) -> Path:
        return Path(self.library_root) if self.library_root else Path.cwd()


def slugify_path(p: Path) -> str:
    """Stable, human-readable slug from an absolute path."""
    p = p.resolve()
    base = re.sub(r"[^A-Za-z0-9]+", "-", str(p)).strip("-").lower()
    digest = hashlib.sha1(str(p).encode()).hexdigest()[:8]
    return f"{base[-40:]}-{digest}" if len(base) > 40 else f"{base}-{digest}"


def resolve_library(library: str | None, root: Path | None = None) -> tuple[str, Path]:
    """Return (slug, library_root). If library is given as an existing slug
    on disk, reuse it; else derive from root (defaults to CWD)."""
    if library:
        existing = STATE_ROOT / library
        if existing.exists():
            cfg_ini = existing / "config.ini"
            if cfg_ini.exists():
                parser = configparser.ConfigParser()
                parser.read(cfg_ini)
                root_str = parser.get("library", "root", fallback="") if parser.has_section("library") else ""
                if root_str:
                    return library, Path(root_str)
            return library, root or Path.cwd()
        return library, root or Path.cwd()
    actual = (root or Path.cwd()).resolve()
    return slugify_path(actual), actual


def _read_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path)
    return parser


def _merge(global_p: configparser.ConfigParser, lib_p: configparser.ConfigParser, section: str, key: str, default: str) -> str:
    if lib_p.has_section(section) and lib_p.has_option(section, key):
        return lib_p.get(section, key)
    if global_p.has_section(section) and global_p.has_option(section, key):
        return global_p.get(section, key)
    return default


def load_config(library: str | None = None, root: Path | None = None) -> Config:
    slug, lib_root = resolve_library(library, root)
    state_dir = STATE_ROOT / slug
    global_p = _read_ini(GLOBAL_CONFIG)
    lib_p = _read_ini(state_dir / "config.ini")
    cfg = Config(
        acoustid_app_api_key=_merge(global_p, lib_p, "acoustid", "api_key", ""),
        musicbrainz_user_agent=_merge(global_p, lib_p, "musicbrainz", "user_agent", "musicorg/0.1 (anonymous)"),
        musicbrainz_contact=_merge(global_p, lib_p, "musicbrainz", "contact_email", ""),
        itunes_country=_merge(global_p, lib_p, "itunes", "country", "us"),
        jiosaavn_user_agent=_merge(global_p, lib_p, "jiosaavn", "user_agent", "Mozilla/5.0"),
        shazam_breaker_threshold=int(_merge(global_p, lib_p, "shazam", "breaker_threshold", "5")),
        shazam_inter_request_sleep_sec=float(_merge(global_p, lib_p, "shazam", "inter_request_sleep_sec", "1.5")),
        default_country=_merge(global_p, lib_p, "defaults", "country", "bollywood"),  # type: ignore[arg-type]
        apply_mode=_merge(global_p, lib_p, "defaults", "apply_mode", "move"),  # type: ignore[arg-type]
        copy_dest=_merge(global_p, lib_p, "defaults", "copy_dest", ""),
        auto_apply_threshold=float(_merge(global_p, lib_p, "defaults", "auto_apply_threshold", "0.86")),
        review_threshold=float(_merge(global_p, lib_p, "defaults", "review_threshold", "0.65")),
        shazam_auto_apply_threshold=float(_merge(global_p, lib_p, "shazam", "auto_apply_threshold", "0.50")),
        exclude_prefixes=[
            x.strip() for x in _merge(global_p, lib_p, "defaults", "exclude_prefixes", "").split(",") if x.strip()
        ],
        library_slug=slug,
        library_root=str(lib_root),
        state_dir=str(state_dir),
    )
    return cfg


def ensure_state_dir(cfg: Config) -> Path:
    p = cfg.state_path
    p.mkdir(parents=True, exist_ok=True)
    (p / "backups").mkdir(exist_ok=True)
    (p / "logs").mkdir(exist_ok=True)
    return p


def save_library_config(cfg: Config, updates: dict[str, dict[str, str]]) -> None:
    """Write/merge updates into the per-library config.ini."""
    ensure_state_dir(cfg)
    path = cfg.state_path / "config.ini"
    parser = _read_ini(path)
    if not parser.has_section("library"):
        parser.add_section("library")
    parser.set("library", "root", cfg.library_root)
    parser.set("library", "slug", cfg.library_slug)
    for section, kv in updates.items():
        if not parser.has_section(section):
            parser.add_section(section)
        for k, v in kv.items():
            parser.set(section, k, str(v))
    with path.open("w") as f:
        parser.write(f)


def save_global_config(updates: dict[str, dict[str, str]]) -> None:
    GLOBAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    parser = _read_ini(GLOBAL_CONFIG)
    for section, kv in updates.items():
        if not parser.has_section(section):
            parser.add_section(section)
        for k, v in kv.items():
            parser.set(section, k, str(v))
    with GLOBAL_CONFIG.open("w") as f:
        parser.write(f)


DEFAULT_EXCLUDE_DIR_NAMES = {"_duplicates", "_misc", "_replaced", "_upgrade_staging", "_organizer"}


def is_path_excluded(path: Path, exclude_prefixes: list[str]) -> bool:
    """True if path lies under any configured exclude prefix or default dir."""
    s = str(path)
    for prefix in exclude_prefixes:
        if not prefix:
            continue
        ap = str(Path(prefix).resolve())
        if s == ap or s.startswith(ap + os.sep):
            return True
    for part in path.parts:
        if part in DEFAULT_EXCLUDE_DIR_NAMES:
            return True
    return False
