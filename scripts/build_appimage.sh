#!/usr/bin/env bash
#
# Build a single-file Linux AppImage for musicorg-gui.
#
# Inputs (assumed present):
#   - .venv/ with project + gui-dev extras installed (PySide6, pyinstaller)
#   - packaging/musicorg-gui.spec, packaging/launcher.py
#   - packaging/AppDir-template/{AppRun,musicorg.desktop,musicorg.svg}
#
# Outputs:
#   - dist/musicorg-gui-x86_64.AppImage
#   - dist/musicorg-gui-x86_64.AppImage.sha256
#
# Usage:
#   scripts/build_appimage.sh [VERSION]
#
# VERSION defaults to the value in src/musicorg_gui/__init__.py.
# The build is reproducible from a clean tree given the same input
# versions; appimagetool itself is downloaded once and cached under
# $XDG_CACHE_HOME/musicorg-appimage-tools/.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Resolve version
if [[ -n "${1:-}" ]]; then
    VERSION="$1"
else
    VERSION="$(grep -oE '"[^"]+"' src/musicorg_gui/__init__.py | head -1 | tr -d '"')"
fi
echo ">>> Building musicorg-gui AppImage  version=$VERSION  arch=x86_64"

# Pick a python — prefer .venv, fall back to system
if [[ -x ".venv/bin/python" ]]; then
    PY=".venv/bin/python"
    PYI=".venv/bin/pyinstaller"
elif [[ -x ".venv/bin/python3" ]]; then
    PY=".venv/bin/python3"
    PYI=".venv/bin/pyinstaller"
else
    PY="python3"
    PYI="pyinstaller"
fi
echo ">>> Using interpreter: $PY"

# Pre-flight: pyinstaller available?
if ! "$PY" -m PyInstaller --version >/dev/null 2>&1; then
    echo "!!! PyInstaller not installed. Run:  $PY -m pip install '.[gui-dev]'" >&2
    exit 2
fi

# 1) Clean previous build state
rm -rf build dist/musicorg-gui dist/AppDir dist/musicorg-gui-*.AppImage

# 2) PyInstaller bundle
echo ">>> PyInstaller: building one-folder bundle"
PYTHONPATH="$ROOT/src" "$PYI" --noconfirm --clean packaging/musicorg-gui.spec

# 3) Assemble AppDir
echo ">>> Assembling AppDir"
APPDIR="$ROOT/dist/AppDir"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps"
mkdir -p "$APPDIR/usr/share/metainfo"

# Copy the PyInstaller bundle into usr/bin/
cp -r dist/musicorg-gui/. "$APPDIR/usr/bin/"

# Top-level AppRun + .desktop + icon (AppImage convention)
cp packaging/AppDir-template/AppRun "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp packaging/AppDir-template/musicorg.desktop "$APPDIR/musicorg.desktop"
cp packaging/AppDir-template/musicorg.svg "$APPDIR/musicorg.svg"

# Also stage them in the standard usr/share/ locations so the AppImage
# integrates cleanly when installed via appimaged / KDE Plasma / GNOME.
cp packaging/AppDir-template/musicorg.desktop "$APPDIR/usr/share/applications/musicorg.desktop"
cp packaging/AppDir-template/musicorg.svg "$APPDIR/usr/share/icons/hicolor/scalable/apps/musicorg.svg"

# 4) Fetch appimagetool if not cached
APPIMAGE_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/musicorg-appimage-tools"
mkdir -p "$APPIMAGE_CACHE"
APPIMAGETOOL="$APPIMAGE_CACHE/appimagetool-x86_64.AppImage"
if [[ ! -x "$APPIMAGETOOL" ]]; then
    echo ">>> Downloading appimagetool"
    curl -fsSL \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
        -o "$APPIMAGETOOL"
    chmod +x "$APPIMAGETOOL"
fi

# 5) Build the AppImage
OUTPUT="dist/musicorg-gui-${VERSION}-x86_64.AppImage"
echo ">>> appimagetool: producing $OUTPUT"

# --appimage-extract-and-run lets appimagetool work without FUSE
# (CI runners typically lack /dev/fuse).
ARCH=x86_64 "$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" "$OUTPUT"

# 6) Checksum
sha256sum "$OUTPUT" > "$OUTPUT.sha256"
echo ">>> sha256: $(cat "$OUTPUT.sha256")"

# 7) Report
SIZE=$(du -h "$OUTPUT" | cut -f1)
echo ">>> Built: $OUTPUT  ($SIZE)"
