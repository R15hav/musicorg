"""Example 01 — basic scan.

Walk a music folder, read tags + compute audio-stream fingerprints,
print a summary. The simplest possible musicorg consumer.

Run:
    python 01_basic_scan.py /path/to/music
"""

from __future__ import annotations
import sys
from pathlib import Path

from musicorg import load_config, scan


def main(music_root: str) -> int:
    # state_root is where musicorg stores its per-library state (CSVs,
    # snapshots, undo scripts). For embedding, pick a path your app owns —
    # /tmp here is fine for a one-shot scan.
    cfg = load_config(state_root=Path("/tmp/musicorg-example-01"))

    tracks = scan(cfg, root=Path(music_root))
    print(f"scanned {len(tracks)} files")

    for t in tracks[:10]:
        fp = t.fingerprint_sha256[:16] if t.fingerprint_sha256 else "<none>"
        print(f"  {fp}  {t.title or '<no title>':30}  {t.artist or '<no artist>'}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else str(Path("../tests/fixtures/library-small").resolve())))
