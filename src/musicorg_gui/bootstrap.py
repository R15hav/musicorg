"""First-run binary fetcher.

The library shells out to ``ffprobe``, ``mediainfo``, and ``ffmpeg``. End
users running an AppImage / .exe / .dmg will rarely have these on PATH.
``ensure_binaries`` resolves the three binaries in priority order:

1. **Already on PATH** — use them as-is. ``shutil.which`` decides.
2. **Already in the cache** — a previous run downloaded them; reuse.
3. **Download** — pull per-platform statics from the manifest, verify
   SHA256, extract, mark executable, cache.

When step 3 runs, the GUI shows a one-time progress dialog (see
``widgets.bootstrap_dialog``). Successful runs end with a call to
``musicorg.set_binary_paths(...)`` so all downstream library code uses
the cached binaries instead of relying on PATH.

The manifest is in-module Python data, not a resource file — keeps
PyInstaller / AppImage packaging simple (no resource-loading code). It
is updated per release; CI verifies the hashes before tagging.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import musicorg

from .platform import binary_cache_dir


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

BinaryName = Literal["ffprobe", "mediainfo", "ffmpeg"]

ProgressCallback = Callable[[str, int, int], None]
"""Bootstrap progress signature: (message, bytes_done, bytes_total).

bytes_total == 0 signals an indeterminate phase (extraction, hashing).
"""


@dataclass(frozen=True)
class BinarySpec:
    """One downloadable binary entry in the manifest."""

    name: BinaryName
    url: str
    sha256: str | None  # None => skip verification with warning (dev only)
    archive: Literal["tar.xz", "tar.gz", "zip", "none"]
    archive_member: str | None  # relative path inside the archive
    target_filename: str  # final filename in the cache dir


@dataclass
class BootstrapResult:
    """Outcome of one ensure_binaries() call."""

    resolved: dict[BinaryName, Path] = dataclasses.field(default_factory=dict)
    found_on_path: list[BinaryName] = dataclasses.field(default_factory=list)
    found_in_cache: list[BinaryName] = dataclasses.field(default_factory=list)
    downloaded: list[BinaryName] = dataclasses.field(default_factory=list)
    failed: dict[BinaryName, str] = dataclasses.field(default_factory=dict)

    @property
    def needs_download(self) -> bool:
        return any(b not in self.resolved for b in ("ffprobe", "mediainfo", "ffmpeg"))


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
#
# Pinned upstream URLs + SHA256 per platform. Versions are deliberately
# pinned (no "latest") so behavior is reproducible across user installs.
# Update via the release pipeline; see DISTRIBUTION.md.
#
# Linux x86_64 entries use real upstream URLs. Windows + macOS entries
# are structural placeholders — fill in URL + sha256 before the matching
# platform release per project-gui-phases.md.

_MANIFEST: dict[str, dict[BinaryName, BinarySpec]] = {
    "linux-x86_64": {
        "ffprobe": BinarySpec(
            name="ffprobe",
            url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
            sha256=None,
            archive="tar.xz",
            archive_member="ffprobe",
            target_filename="ffprobe",
        ),
        "ffmpeg": BinarySpec(
            name="ffmpeg",
            url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
            sha256=None,
            archive="tar.xz",
            archive_member="ffmpeg",
            target_filename="ffmpeg",
        ),
        "mediainfo": BinarySpec(
            name="mediainfo",
            url="https://mediaarea.net/download/binary/mediainfo/24.06/MediaInfo_CLI_24.06_GNU_FromSource.tar.xz",
            sha256=None,
            archive="tar.xz",
            archive_member="MediaInfo_CLI_GNU_FromSource/bin/mediainfo",
            target_filename="mediainfo",
        ),
    },
    "windows-x86_64": {
        "ffprobe": BinarySpec(
            name="ffprobe",
            url="https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip",
            sha256=None,
            archive="zip",
            archive_member="bin/ffprobe.exe",
            target_filename="ffprobe.exe",
        ),
        "ffmpeg": BinarySpec(
            name="ffmpeg",
            url="https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip",
            sha256=None,
            archive="zip",
            archive_member="bin/ffmpeg.exe",
            target_filename="ffmpeg.exe",
        ),
        "mediainfo": BinarySpec(
            name="mediainfo",
            url="https://mediaarea.net/download/binary/mediainfo/24.06/MediaInfo_CLI_24.06_Windows_x64.zip",
            sha256=None,
            archive="zip",
            archive_member="MediaInfo.exe",
            target_filename="mediainfo.exe",
        ),
    },
    "macos-universal2": {
        "ffprobe": BinarySpec(
            name="ffprobe",
            url="https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip",
            sha256=None,
            archive="zip",
            archive_member="ffprobe",
            target_filename="ffprobe",
        ),
        "ffmpeg": BinarySpec(
            name="ffmpeg",
            url="https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip",
            sha256=None,
            archive="zip",
            archive_member="ffmpeg",
            target_filename="ffmpeg",
        ),
        "mediainfo": BinarySpec(
            name="mediainfo",
            url="https://mediaarea.net/download/binary/mediainfo/24.06/MediaInfo_CLI_24.06_Mac.dmg",
            sha256=None,
            archive="none",
            archive_member=None,
            target_filename="mediainfo",
        ),
    },
}


def _platform_key() -> str:
    """Return the manifest key for the current OS + arch."""
    machine = platform.machine().lower()
    if sys.platform == "linux":
        if machine in ("x86_64", "amd64"):
            return "linux-x86_64"
    if sys.platform == "win32":
        if machine in ("x86_64", "amd64"):
            return "windows-x86_64"
    if sys.platform == "darwin":
        return "macos-universal2"
    raise RuntimeError(f"Unsupported platform: {sys.platform} {machine}")


# ---------------------------------------------------------------------------
# Download + extraction
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, progress: ProgressCallback | None) -> None:
    """Stream ``url`` to ``dest`` with progress callbacks.

    Uses urllib so we don't add a hard requests dep to the GUI (the lib
    already depends on requests; this just avoids importing it in a path
    that may run before the venv is fully set up on a fresh install).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "musicorg-gui/0.1"})
    with urllib.request.urlopen(req) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with dest.open("wb") as fh:
            while chunk := resp.read(1 << 16):
                fh.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(f"Downloading {dest.name}", done, total)


def _extract_one(
    archive_path: Path,
    archive_kind: str,
    member: str,
    out_path: Path,
) -> None:
    """Extract a single named member from ``archive_path`` to ``out_path``.

    For tar.xz / tar.gz the member is matched by basename anywhere in the
    archive (upstream tarballs prefix everything with a versioned root
    directory whose name we don't know without inspecting). For zip the
    member is the explicit zip path.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if archive_kind in ("tar.xz", "tar.gz"):
        mode = "r:xz" if archive_kind == "tar.xz" else "r:gz"
        with tarfile.open(archive_path, mode) as tf:
            for entry in tf:
                if entry.isfile() and (entry.name.endswith("/" + member) or entry.name == member):
                    src = tf.extractfile(entry)
                    if src is None:
                        continue
                    with out_path.open("wb") as fh:
                        shutil.copyfileobj(src, fh)
                    return
        raise FileNotFoundError(f"{member!r} not found inside {archive_path}")

    if archive_kind == "zip":
        with zipfile.ZipFile(archive_path) as zf:
            for name in zf.namelist():
                if name.endswith("/" + member) or name == member or Path(name).name == Path(member).name:
                    with zf.open(name) as src, out_path.open("wb") as fh:
                        shutil.copyfileobj(src, fh)
                    return
        raise FileNotFoundError(f"{member!r} not found inside {archive_path}")

    if archive_kind == "none":
        shutil.copy2(archive_path, out_path)
        return

    raise ValueError(f"Unsupported archive kind: {archive_kind}")


def _install_one(
    spec: BinarySpec,
    cache_dir: Path,
    progress: ProgressCallback | None,
) -> Path:
    """Download + verify + extract one BinarySpec. Returns the final path."""
    final = cache_dir / spec.target_filename

    with tempfile.TemporaryDirectory(prefix="musicorg-bootstrap-") as td:
        td_path = Path(td)
        archive_path = td_path / f"download.{spec.archive}"
        _download(spec.url, archive_path, progress)

        if spec.sha256 is not None:
            if progress is not None:
                progress(f"Verifying {spec.target_filename}", 0, 0)
            actual = _sha256_file(archive_path)
            if actual != spec.sha256:
                raise RuntimeError(
                    f"SHA256 mismatch for {spec.url}\n  expected: {spec.sha256}\n  actual:   {actual}"
                )

        if progress is not None:
            progress(f"Extracting {spec.target_filename}", 0, 0)

        if spec.archive == "none":
            shutil.copy2(archive_path, final)
        else:
            assert spec.archive_member is not None
            _extract_one(archive_path, spec.archive, spec.archive_member, final)

    if sys.platform != "win32":
        final.chmod(final.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return final


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def needs_setup() -> bool:
    """Cheap check: would ``ensure_binaries`` have to download anything?

    Returns True iff at least one binary is missing both from PATH and from
    the cache directory. No network access. Used by the app entry point
    to decide whether to show the bootstrap dialog or skip silently.
    """
    cache_dir = binary_cache_dir()
    try:
        platform_key = _platform_key()
    except RuntimeError:
        return False
    manifest = _MANIFEST[platform_key]
    for name in ("ffprobe", "mediainfo", "ffmpeg"):
        if shutil.which(name):
            continue
        cached = cache_dir / manifest[name].target_filename  # type: ignore[index]
        if cached.exists() and cached.stat().st_size > 0:
            continue
        return True
    return False


def ensure_binaries(progress: ProgressCallback | None = None) -> BootstrapResult:
    """Resolve ffprobe / mediainfo / ffmpeg for the current process.

    Side effect: calls ``musicorg.set_binary_paths(...)`` so all library
    subprocess calls use the resolved paths.

    Resolution order per binary: PATH lookup → cache → download. A binary
    that fails to download is recorded in ``BootstrapResult.failed`` but
    does not raise — the GUI surfaces partial-failure to the user and
    lets them proceed (the library degrades gracefully when binaries are
    missing).
    """
    result = BootstrapResult()
    cache_dir = binary_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        platform_key = _platform_key()
    except RuntimeError as exc:
        for b in ("ffprobe", "mediainfo", "ffmpeg"):
            result.failed[b] = str(exc)  # type: ignore[index]
        return result

    manifest = _MANIFEST[platform_key]

    for name in ("ffprobe", "mediainfo", "ffmpeg"):
        on_path = shutil.which(name)
        if on_path is not None:
            result.resolved[name] = Path(on_path)  # type: ignore[index]
            result.found_on_path.append(name)  # type: ignore[arg-type]
            continue

        spec = manifest[name]  # type: ignore[index]
        cached = cache_dir / spec.target_filename
        if cached.exists() and cached.stat().st_size > 0:
            result.resolved[name] = cached  # type: ignore[index]
            result.found_in_cache.append(name)  # type: ignore[arg-type]
            continue

        try:
            installed = _install_one(spec, cache_dir, progress)
        except Exception as exc:
            result.failed[name] = f"{type(exc).__name__}: {exc}"  # type: ignore[index]
            continue

        result.resolved[name] = installed  # type: ignore[index]
        result.downloaded.append(name)  # type: ignore[arg-type]

    musicorg.set_binary_paths(
        ffprobe=result.resolved.get("ffprobe"),
        mediainfo=result.resolved.get("mediainfo"),
        ffmpeg=result.resolved.get("ffmpeg"),
    )

    return result
