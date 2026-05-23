# Architecture

This page is for contributors and embedders. It explains the library/CLI split, how the pipeline phases connect, the state-directory layout, and the undo machinery.

See [PUBLIC_API.md](https://github.com/R15hav/musicorg/blob/main/PUBLIC_API.md) for the formal stability contract.

---

## Library / CLI split

musicorg is two Python packages:

| Package | Contents | Dependencies |
|---|---|---|
| `musicorg` | All pipeline logic, models, config, lookup tiers, upgrade orchestration | `mutagen`, `requests`; `shazamio` optional |
| `musicorg_cli` | Typer CLI entry point, wizard, four Textual TUIs | `typer`, `textual`, `rich` |

`musicorg_cli` is a **reference consumer** of the library. It imports from `musicorg.*` but the library has zero knowledge of the CLI. This means you can embed the library in a FastAPI service, a PySide6 desktop app, or a headless script without pulling in Typer or Textual.

The `[cli]` extra installs both packages:

```bash
pip install musicorg[cli]   # installs musicorg + musicorg_cli
pip install musicorg         # library only
```

---

## Pipeline phases

The pipeline is a linear sequence of CSV-producing phases. Each phase reads from previous-phase output and writes its own output files. There is no shared in-memory state between phases; everything flows through the state directory.

```
scan()              → 01_tags.csv
group_duplicates()  → 07_winners.csv, 07_duplicates.csv, 07_groups.csv
resolve_winners()   → 08_resolved.csv
plan()              → 09_plan.csv
execute_plan()      → moves/copies files; writes undo_<TS>.sh
chain()             → 16_merged.csv  (one row per file, all tier results)
build_diff()        → 17_dryrun_diff.csv
apply_canonical()   → writes tags + renames; undo_phase18_<TS>.py
apply_approvals()   → writes tags + renames; undo_phase20_<TS>.py
refingerprint()     → 30_shazam_refingerprint.csv
upgrade_batch()     → replaces lossy files; upgrade_skips.csv
```

Any phase can be re-run without re-running earlier phases. The wizard runs them in order; the CLI exposes each as a standalone subcommand.

---

## The lookup tier chain

`chain(track, cfg)` in `musicorg/lookup/__init__.py` tries each tier in order and short-circuits at the first result with auto-apply confidence (≥ 0.86 by default):

```
iTunes Search API  →  JioSaavn  →  Shazam
```

Scoring is done by `score_match()` (for iTunes and JioSaavn) and `score_shazam()`. Sub-scores:

- Title: weight 0.55
- Artist: weight 0.25
- Duration: weight 0.20
- Album bonus, year penalty, version-marker penalty, sparse cap

Decisions: `auto_apply` (≥ 0.86), `review` (≥ 0.65), `low` (< 0.65), `no_match` (no result).

The Shazam tier is protected by a `CircuitBreaker`. After 5 consecutive failures it writes a marker file and is skipped on all subsequent runs until the marker is deleted.

---

## The extension protocol

Third parties implement `UpgradeExtension` to add new upgrade sources (Qobuz, Tidal, Bandcamp, local archive). The protocol is `@runtime_checkable` — no subclassing:

```python
from musicorg import UpgradeExtension, UpgradeCandidate, UpgradeResult, PreflightResult

class MyExtension:
    name = "my-source"
    protocol_version = "1.0"

    def preflight(self) -> PreflightResult: ...
    def supports(self, candidate: UpgradeCandidate) -> bool: ...
    def upgrade(self, candidate: UpgradeCandidate) -> UpgradeResult: ...
    def cleanup(self) -> None: ...
```

`UpgradeCandidate` is a frozen dataclass. Identity is always `fingerprint_sha256` — never a file path, because files may have been renamed by earlier pipeline phases.

The currently supported protocol version is declared in `SUPPORTED_PROTOCOL_VERSIONS` (currently `{"1.0"}`). Check the version before starting the batch loop:

```python
assert ext.protocol_version in SUPPORTED_PROTOCOL_VERSIONS
```

See [`src/musicorg/extensions/protocol.py`](https://github.com/R15hav/musicorg/blob/main/src/musicorg/extensions/protocol.py) and [`examples/04_custom_extension.py`](https://github.com/R15hav/musicorg/blob/main/examples/04_custom_extension.py).

---

## State directory layout

For a library named `home` (state root `~/.local/share/musicorg/`):

```
~/.local/share/musicorg/home/
├── config.ini                        per-library config overrides
├── 01_tags.csv                       scan output
├── 07_winners.csv                    dedupe winners
├── 07_duplicates.csv                 dedupe losers
├── 07_groups.csv                     duplicate group summaries
├── 08_resolved.csv                   resolve output
├── 09_plan.csv                       plan output
├── 16_merged.csv                     canonicalize: all tier results per file
├── 17_dryrun_diff.csv                canonical-apply: tag diff preview
├── 19_review.csv                     user-edited approval CSV
├── 19_approvals.json                 parsed approvals
├── 30_shazam_refingerprint.csv       refingerprint: Shazam hits + Apple Music URLs
├── upgrade_skips.csv                 permanent-skip records
├── backups/
│   ├── tag_snapshot_<TS>.json        legacy path-keyed tag snapshot
│   └── tag_snapshot_phase20_<TS>.json
├── logs/
│   └── <phase>.log
├── undo_<TS>.sh                      file-move undo (bash)
├── undo_phase18_<TS>.py              tag-write undo (Python, auto_apply)
├── undo_phase20_<TS>.py              tag-write undo (Python, approvals)
└── undo_upgrade_<TS>.py              ALAC upgrade undo (Python)
```

Global config: `~/.config/musicorg/config.ini`

Embedders can override the state root by passing `state_root=Path(...)` to `load_config()`. This isolates the library's state from the system XDG tree — useful in Docker, Electron, tests, or any environment where you want a controlled state path.

---

## The undo machinery

musicorg generates three kinds of undo artifacts:

**1. Shell undo scripts (`undo_<TS>.sh`)** — generated by `execute_plan()` for file moves. Each line is a `mv` command that reverses one move. Plain bash, auditable, runnable directly.

**2. Thin Python undo scripts (`undo_phase*.py`)** — generated by `generate_undo_script()` for tag writes. The script reads a sibling ops JSON file at runtime:

```
backups/tag_snapshot_<TS>.json   ← full pre-write tag snapshot
undo_phase18_<TS>.py             ← thin script: reads snapshot + ops, restores
```

The script never inlines the ops data. This was a hard lesson from the original 34-script pipeline where inlining produced 41 MB scripts. At runtime, the Python script loads the JSON, looks up each file's pre-write state in the snapshot, and calls `write_tags()` to restore.

**3. `SnapshotStore` (SQLite)** — the newer snapshot mechanism for new code. Keyed by `fingerprint_sha256` rather than path, so it survives renames. Excludes large binary frames (`APIC`, `covr`, `GEOB`, `PRIV`, `USLT`) by default to keep the database compact. See `DEFAULT_EXCLUDED_FRAMES`.

The legacy JSON snapshots (`snapshot_tags`, `restore_from_snapshot`, `list_snapshots`) are still exported and used by the CLI but are scheduled for removal at v1.0. New code should use `SnapshotStore`.

---

## Content-addressed identity

Every pipeline phase uses `fingerprint_sha256` — the SHA-256 of the demuxed audio stream — as the join key between phases. This is computed by `audio_stream_sha256()` which calls ffmpeg to demux the stream before hashing.

Why audio-stream rather than file hash:
- Tag-only rewrites (writing ID3 tags) change the file hash but not the audio stream. The fingerprint survives tag writes.
- Renames change the path entirely. The fingerprint is stable across moves and renames.

The fallback (when ffmpeg fails) is `fallback_<sha256-of-whole-file>_<mtime-ms>`. `identity_quality()` distinguishes primary (audio-stream) from fallback (whole-file) fingerprints.

---

## ProgressEvent callbacks

Every long-running phase accepts a `progress` callback:

```python
def on_progress(ev: ProgressEvent) -> None:
    print(f"{ev.phase} {ev.current}/{ev.total} {ev.path}")

tracks = scan(cfg, root=root, progress=on_progress)
```

`ev.error = True` flags a per-file failure that did not abort the phase. Fatal errors raise instead.

`ProgressEvent` is the single push-based shape consumed by the Textual TUI, the CLI's Rich progress bars, and the FastAPI WebSocket bridge (see `examples/05_embed_in_fastapi.py`).
