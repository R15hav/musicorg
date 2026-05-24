# PyInstaller spec for the musicorg desktop GUI.
#
# Builds a one-folder bundle (faster startup than --onefile, and the
# AppImage wraps the whole folder anyway). The same spec drives the
# Linux AppImage today; Windows .exe and macOS .app reuses come later
# in the locked phasing order.
#
# Run from the project root:
#   pyinstaller --noconfirm packaging/musicorg-gui.spec
#
# Output: dist/musicorg-gui/musicorg-gui (+ _internal/ directory)

# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

import sys

# spec files are exec'd; __file__ is not always defined. Resolve via SPEC.
SPEC_DIR = Path(SPEC).resolve().parent  # type: ignore[name-defined]  # noqa: F821
PROJECT_ROOT = SPEC_DIR.parent
SRC = PROJECT_ROOT / "src"

block_cipher = None

a = Analysis(
    [str(SPEC_DIR / "launcher.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Library — pulled in via musicorg_gui imports, but pin defensively.
        "musicorg",
        "musicorg.lookup",
        "musicorg.lookup.itunes",
        "musicorg.lookup.jiosaavn",
        "musicorg.lookup.shazam",
        # mutagen submodules — loaded by format.
        "mutagen.id3",
        "mutagen.mp4",
        "mutagen.flac",
        "mutagen.oggvorbis",
        "mutagen.oggopus",
        "mutagen.wave",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Exclude shazamio + gamdl — they're optional extras the user installs
    # separately (and gamdl needs system credentials anyway). Excluding
    # keeps the bundle ~15 MB smaller.
    excludes=[
        "shazamio",
        "gamdl",
        # Test machinery — keep the bundle lean.
        "pytest",
        "pytest_asyncio",
        # Other Python desktop frameworks PyInstaller sometimes detects.
        "tkinter",
        "tcl",
        "tk",
    ],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="musicorg-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # No terminal window on Windows; AppImage on Linux ignores.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="musicorg-gui",
)
