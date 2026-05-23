# musicorg examples

Worked examples for embedding the `musicorg` library in your own application.
Every example is self-contained and runnable from this directory:

    pip install -e ../
    python 01_basic_scan.py /path/to/some/music

## 60-second quickstart

```python
from pathlib import Path
from musicorg import load_config, scan

cfg = load_config(state_root=Path("/tmp/musicorg-quickstart"))
tracks = scan(cfg, root=Path("/path/to/music"))
for t in tracks[:5]:
    print(t.fingerprint_sha256[:16], t.title, "—", t.artist)
```

That's it. Three lines plus your music folder.

## Examples

| # | File | What it shows |
|---|------|---------------|
| 01 | [01_basic_scan.py](01_basic_scan.py) | Walk a folder, read tags, get fingerprints. The minimum. |
| 02 | [02_progress_callback.py](02_progress_callback.py) | Receive `ProgressEvent` per file — for progress bars / status updates. |
| 03 | [03_full_pipeline.py](03_full_pipeline.py) | Scan → dedupe → resolve → plan, the end-to-end flow (dry-run by default). |
| 04 | [04_custom_extension.py](04_custom_extension.py) | Implement `UpgradeExtension` to add local-FLAC archive support. |
| 05 | [05_embed_in_fastapi.py](05_embed_in_fastapi.py) | Wrap musicorg in a FastAPI web app skeleton. |
| 06 | [06_embed_in_pyside.py](06_embed_in_pyside.py) | Drive musicorg from a PySide6 desktop GUI skeleton. |
| 07 | [typescript-client/README.md](typescript-client/README.md) | Generate a TypeScript SDK from the OpenAPI schema (when the daemon ships). |

## Patterns

These show up across the examples — worth knowing before you read them:

- **`Config` is injected, not global.** Pass `state_root=Path(...)` to put state where you want it (Docker volume, user's app data dir, `/tmp` for tests). Each example uses a unique `/tmp/musicorg-example-NN` path to avoid cross-contamination.
- **Progress is push-based.** Every long-running phase accepts `progress: Callable[[ProgressEvent], None]`. Pass `None` to silence; pass a callback to stream events to a progress bar, logger, or WebSocket.
- **Logging is stdlib.** Attach `logging.basicConfig(level=logging.INFO)` or a custom handler at your app's entry point. The library calls `logging.getLogger("musicorg.<module>")` and never installs handlers itself, so you control the output.
- **Extension protocol is duck-typed.** Implement `UpgradeExtension`'s four methods (`preflight`, `supports`, `upgrade`, `cleanup`) — no inheritance required, only structural compatibility.
- **State files are CSV.** Each pipeline phase reads and writes a numbered CSV (`01_tags.csv`, `07_winners.csv`, `08_resolved.csv`, `09_plan.csv`). All paths are under `state_root`, which makes them inspectable, diffable, and version-controllable.

## Read also

- [`../PUBLIC_API.md`](../PUBLIC_API.md) — the library's stable API surface (76 names).
- [`../_organizer/LIBRARY_PLAN.md`](../_organizer/LIBRARY_PLAN.md) — architecture and design decisions.
- [`../_organizer/optimization.md`](../_organizer/optimization.md) — pipeline architecture and CORE-* patterns.
