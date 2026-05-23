"""Cross-platform helpers — state directories, binary cache, file-manager open.

The musicorg library defaults to XDG (Linux) for state. The GUI runs on
Linux, Windows, and macOS, so paths are resolved through ``platformdirs``
and passed into the library via ``Config(state_root=..., ...)``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import platformdirs


_APP_NAME = "musicorg"
_APP_AUTHOR = "musicorg"


def state_root() -> Path:
    """Per-user data directory for library state.

    Linux:   ~/.local/share/musicorg
    Windows: %APPDATA%\\musicorg
    macOS:   ~/Library/Application Support/musicorg
    """
    return Path(platformdirs.user_data_dir(_APP_NAME, _APP_AUTHOR))


def global_config_path() -> Path:
    """Global ``config.ini`` location, per platform conventions.

    Linux:   ~/.config/musicorg/config.ini
    Windows: %APPDATA%\\musicorg\\config.ini
    macOS:   ~/Library/Application Support/musicorg/config.ini
    """
    return Path(platformdirs.user_config_dir(_APP_NAME, _APP_AUTHOR)) / "config.ini"


def binary_cache_dir() -> Path:
    """Where downloaded ffprobe/mediainfo/ffmpeg binaries live.

    Linux:   ~/.cache/musicorg/bin
    Windows: %LOCALAPPDATA%\\musicorg\\Cache\\bin
    macOS:   ~/Library/Caches/musicorg/bin
    """
    return Path(platformdirs.user_cache_dir(_APP_NAME, _APP_AUTHOR)) / "bin"


def reveal_in_file_manager(path: Path) -> None:
    """Open the OS file manager with ``path`` selected (best effort)."""
    p = str(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", p])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer.exe", f"/select,{p}"])
    else:
        target = p if Path(p).is_dir() else str(Path(p).parent)
        subprocess.Popen(["xdg-open", target])


def symlink_supported() -> bool:
    """True if ``os.symlink`` works for the current user on this platform.

    Hidden in the Apply-mode dropdown when False (Windows without Developer
    Mode or admin). See [[project-gui-scope]].
    """
    if sys.platform != "win32":
        return True
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src"
        dst = Path(td) / "dst"
        src.write_text("")
        try:
            os.symlink(src, dst)
        except OSError:
            return False
        return True
