"""Example 02 — progress callback.

Every long-running phase in musicorg accepts a `progress` callback that
receives one `ProgressEvent` per file. Use it for progress bars, status
lines, or pushing events to a web UI.

Run:
    python 02_progress_callback.py /path/to/music
"""

from __future__ import annotations
import sys
from pathlib import Path

from musicorg import load_config, scan, ProgressEvent


def main(music_root: str) -> int:
    cfg = load_config(state_root=Path("/tmp/musicorg-example-02"))

    # Option A — print a status line per file, overwriting in place.
    # ev.error is True when a single file failed but the scan continued.
    def on_progress(ev: ProgressEvent) -> None:
        marker = "!" if ev.error else " "
        # Truncate the path to the rightmost 60 characters so it fits one line.
        path_tail = ev.path[-60:] if len(ev.path) > 60 else ev.path
        print(f"\r[{marker}] {ev.phase} {ev.current}/{ev.total}  {path_tail}", end="", flush=True)

    tracks = scan(cfg, root=Path(music_root), progress=on_progress)
    print()  # newline after the final \r
    print(f"done — {len(tracks)} files")

    # Option B — drop events into a queue (great for web UI WebSockets).
    # The WebSocket handler drains q on a background thread and serializes
    # each ProgressEvent to JSON for the browser.
    #
    # import queue
    # q: queue.Queue[ProgressEvent] = queue.Queue()
    # scan(cfg, root=Path(music_root), progress=q.put)

    # Option C — tqdm progress bar (requires `pip install tqdm`).
    #
    # from tqdm import tqdm
    # bar: tqdm | None = None
    # def on_tqdm(ev: ProgressEvent) -> None:
    #     nonlocal bar
    #     if bar is None:
    #         bar = tqdm(total=ev.total, desc=ev.phase, unit="file")
    #     bar.update(1)
    #     if ev.current >= ev.total and bar:
    #         bar.close()
    # scan(cfg, root=Path(music_root), progress=on_tqdm)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else str(Path("../tests/fixtures/library-small").resolve())))
