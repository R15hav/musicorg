# musicorg

A pure-Python library that organises a messy music collection end-to-end: scan, dedupe, resolve metadata, plan a destination tree, write canonical tags, and optionally upgrade lossy tracks to ALAC. Every phase returns structured data and emits `ProgressEvent` callbacks so you can embed it in any app — CLI, web service, desktop GUI, or script.

The companion CLI (`musicorg_cli`) ships a guided wizard, a Typer power-user interface, and four Textual TUIs; install it with `pip install musicorg[cli]` and see the [docs site](https://R15hav.github.io/musicorg/) for end-user instructions.

---

## 60-second quickstart

```bash
pip install musicorg
```

**Scan a folder:**

```python
from pathlib import Path
from musicorg import load_config, scan

cfg = load_config(state_root=Path("/tmp/my-musicorg-state"))
tracks = scan(cfg, root=Path("/home/user/Music"))

for t in tracks[:5]:
    fp = t.fingerprint_sha256[:16] if t.fingerprint_sha256 else "<none>"
    print(fp, t.title or "<no title>", "-", t.artist or "<no artist>")
```

**Full pipeline (scan → dedupe → resolve → plan → apply tags):**

```python
from pathlib import Path
from musicorg import (
    load_config,
    ensure_state_dir,
    scan,
    write_tags_csv,
    read_tags_csv,
    group_duplicates,
    write_dedupe_outputs,
    resolve_winners,
    plan,
    build_diff,
    apply_canonical,
)

cfg = load_config(state_root=Path("/tmp/my-musicorg-state"))
state_dir = ensure_state_dir(cfg)
root = Path("/home/user/Music")

# Phase 1 — scan
tracks = scan(cfg, root=root)
tags_csv = state_dir / "01_tags.csv"
write_tags_csv(tracks, tags_csv)

# Phase 2 — dedupe
rows = read_tags_csv(tags_csv)
winners, losers, groups = group_duplicates(rows)
paths = write_dedupe_outputs(winners, losers, groups, state_dir)

# Phase 3 — resolve folder/tag/filename evidence
resolved_csv = state_dir / "08_resolved.csv"
resolve_winners(paths["winners"], resolved_csv)

# Phase 4 — plan destination tree
plan_csv = state_dir / "09_plan.csv"
plan(resolved_csv, plan_csv, music_root=root / "Music")

# Phase 5 — apply (dry_run=True by default; pass dry_run=False to commit)
merged_csv = state_dir / "16_merged.csv"  # written by the lookup phase
if merged_csv.exists():
    diff_csv = state_dir / "17_dryrun_diff.csv"
    build_diff(merged_csv, diff_csv)
    result = apply_canonical(
        diff_csv, merged_csv,
        snapshot_path=state_dir / "backups" / "snapshot.db",
        undo_script_path=state_dir / "backups" / "undo.py",
        dry_run=True,
    )
    print(result)
```

See [`examples/03_full_pipeline.py`](examples/03_full_pipeline.py) for the complete annotated version.

---

## What's exported

`musicorg.__all__` exports the full public surface: configuration (`Config`, `load_config`, `save_library_config`, `save_global_config`, `ensure_state_dir`), models (`Track`, `ResolvedTrack`, `TierMatch`, `ApplyResult`, `ProgressEvent`, `SkipReason`), all phase functions, cleaning utilities, approval helpers, lookup tier functions, and the extension protocol types.

[`PUBLIC_API.md`](PUBLIC_API.md) is the formal stability contract. It documents every exported name, SemVer rules (strict after v1.0; breaking changes permitted in minor versions pre-v1.0), and the list of provisional names.

---

## The extension protocol

Third parties can teach musicorg about new upgrade sources — Qobuz, Tidal, Bandcamp, a local FLAC archive — by implementing the `UpgradeExtension` protocol. No subclassing is required: `UpgradeExtension` is `@runtime_checkable`, so a plain class that implements four methods (`preflight`, `supports`, `upgrade`, `cleanup`) and two attributes (`name`, `protocol_version`) passes `isinstance` checks.

Extensions receive a frozen `UpgradeCandidate` (identity is `fingerprint_sha256`, never a path) and return an `UpgradeResult`. `preflight()` runs once before the batch loop; `cleanup()` is guaranteed to run even on `KeyboardInterrupt`. The currently supported protocol version is declared in `SUPPORTED_PROTOCOL_VERSIONS`.

See [`src/musicorg/extensions/protocol.py`](src/musicorg/extensions/protocol.py) for the protocol definition and [`examples/04_custom_extension.py`](examples/04_custom_extension.py) for a working local-FLAC-archive implementation.

---

## The CLI, wizard, and TUIs

A reference command-line consumer ships as `musicorg_cli` and is installed via the `[cli]` extra:

```bash
pip install musicorg[cli]
```

Running `musicorg` with no arguments drops into a guided three-stage wizard (file-tree organizer → canonical metadata → optional ALAC upgrade). Power users can drive individual phases via ~20 Typer subcommands. Four Textual TUIs handle review, approval, and duplicate-picking workflows.

Full installation guide, wizard walkthrough, CLI reference, and TUI docs live at **[https://R15hav.github.io/musicorg/](https://R15hav.github.io/musicorg/)**.

---

## Repo layout

```
src/musicorg/                   library — pure Python, only mutagen + requests
├── clean.py                    junk-stripping, query prep, version markers
├── tags.py                     mutagen → ffprobe → mediainfo cascade + writers
├── identity.py                 audio-stream SHA-256 (content-addressed join key)
├── models.py                   Track, ResolvedTrack, TierMatch, ApplyResult, ProgressEvent, SkipReason
├── config.py                   XDG-Linux config + per-library state dirs
├── scan.py                     walk + per-file tag read
├── dedupe.py                   group + score winners
├── resolve.py                  folder/tag/filename reconciliation + country heuristics
├── planner.py                  destination tree + album-count demotion
├── executor.py                 move/copy/symlink + collision handling + undo .sh
├── canonicalize.py             diff + apply + year guardrail
├── backup.py                   SnapshotStore (SQLite) + thin undo script generator
├── approval.py                 CSV round-trip + batch rule
├── upgrade.py                  gamdl orchestration + ffprobe verification + skip taxonomy
├── refingerprint.py            Shazam refingerprint pass + orphan recovery
├── lookup/                     iTunes, JioSaavn, Shazam tiers + scoring + circuit breaker
└── extensions/protocol.py     UpgradeExtension, UpgradeCandidate, UpgradeResult, PreflightResult

src/musicorg_cli/               reference CLI — Typer + Textual + Rich (install via [cli] extra)

examples/                       embedding patterns (FastAPI, PySide6, custom extension, full pipeline)
tests/fixtures/build_fixture.py regenerates the demo library
PUBLIC_API.md                   library stability contract (SemVer after v1.0)
DISTRIBUTION.md                 release channels and packaging plan
install.sh                      distro-aware installer (for end-user installs from a clone)
```

---

## Versioning

Current version: `0.2.0` (pre-v1.0; API stabilisation in progress). Breaking changes are permitted in minor versions before v1.0. After v1.0, strict SemVer applies. See [`PUBLIC_API.md`](PUBLIC_API.md) for the exact rules and the list of names flagged provisional.

---

## License

MIT.
