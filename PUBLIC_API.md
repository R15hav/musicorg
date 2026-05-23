# musicorg public API (v0.2.0)

This document is the **stability contract** for the musicorg Python library. Every name listed here is re-exported from `musicorg/__init__.py` and follows Semantic Versioning after v1.0.0. Names prefixed with `_` are internal and may change between minor versions.

Current version: `0.2.0` (pre-v1.0; API stabilization in progress).

## SemVer rules

- **Major bump** required to: remove a name, narrow a return type, add a required parameter, shrink an append-only list (`SITE_JUNK_PATTERNS`, `SkipReason` members, `DEFAULT_EXCLUDED_FRAMES`, `SUPPORTED_PROTOCOL_VERSIONS`).
- **Minor bump** for: adding new names, adding optional parameters, broadening return types, growing append-only lists.
- **Patch bump** for: bug fixes that preserve all of the above.

## Public surface

### Configuration

| Name | Kind | Summary |
|---|---|---|
| `Config` | dataclass | Per-library + global configuration record. State path, thresholds, tier user-agents. `state_root` overrides XDG defaults for embedders. |
| `load_config(library=None, root=None, state_root=None, global_config_path=None) -> Config` | function | Load and merge global + per-library INI config. XDG defaults apply when `state_root` and `global_config_path` are None. |
| `save_library_config(cfg, updates) -> None` | function | Write or update the per-library `config.ini` with a `{section: {key: value}}` dict. |
| `save_global_config(updates, path=None) -> None` | function | Write or update the global `config.ini`. Uses XDG default path when `path` is None. |
| `ensure_state_dir(cfg) -> Path` | function | Create `state/`, `backups/`, and `logs/` under the library state directory. Idempotent. |

### Runtime binaries

| Name | Kind | Summary |
|---|---|---|
| `set_binary_paths(*, ffprobe=None, mediainfo=None, ffmpeg=None) -> None` | function | Override the paths the library uses to invoke `ffprobe`, `mediainfo`, and `ffmpeg`. Each argument may be a bare command name (relies on `PATH`) or an absolute path; `None` leaves a binary unchanged. Process-global; call once at startup. Environment variables `MUSICORG_FFPROBE`, `MUSICORG_MEDIAINFO`, `MUSICORG_FFMPEG` provide import-time defaults. |

### Models

| Name | Kind | Summary |
|---|---|---|
| `Track` | dataclass | Per-file record from `scan()`. `fingerprint_sha256` is the content-addressed join key (CORE-11). |
| `ResolvedTrack` | dataclass | Folder+tag fusion output from the resolve phase. `needs_lookup` lists missing fields. |
| `TierMatch` | dataclass | Best-match record from a single lookup tier. Carries confidence, raw API response, and score sub-parts. |
| `ApplyResult` | dataclass | Counters returned by `apply_canonical` / `apply_approvals`. Includes `snapshot_path` and `undo_script_path`. |
| `ProgressEvent` | dataclass | Push-based progress callback payload. `phase`, `current`, `total`, `path`, `message`, `error`. |
| `SkipReason` | enum | Closed taxonomy of permanent-skip reasons (append-only). String-valued for direct CSV persistence. |

### Identity

| Name | Kind | Summary |
|---|---|---|
| `audio_stream_sha256(path) -> str` | function | SHA256 of the demuxed audio stream. Survives tag-only rewrites and renames. Returns `"fallback_<sha>_<ms>"` on ffmpeg failure. |
| `identity_quality(fingerprint) -> Literal["primary", "fallback"]` | function | Classify a fingerprint as primary (audio-stream) or fallback (whole-file). |

### Core phases

#### Scan

| Name | Kind | Summary |
|---|---|---|
| `scan(cfg, root=None, extra_excludes=None, progress=None, compute_fingerprint=True) -> list[Track]` | function | Walk the library and return one `Track` per audio file. Computes `fingerprint_sha256` per file unless `compute_fingerprint=False`. |
| `write_tags_csv(tracks, out_path) -> ScanStats` | function | Persist `Track` list to `01_tags.csv` (one row per file). Returns coverage stats. |
| `compute_stats(tracks) -> ScanStats` | function | Bucket per-file `tag_source` codes into a `ScanStats` counter. |

#### Dedupe

| Name | Kind | Summary |
|---|---|---|
| `group_duplicates(tracks_csv_rows) -> tuple[list[dict], list[dict], list[dict]]` | function | Cluster rows by normalized `title|artist` + duration bucket. Returns `(winners, losers, group_summaries)`. |
| `read_tags_csv(path) -> list[dict]` | function | Load a tags CSV produced by `write_tags_csv`. |
| `write_dedupe_outputs(winners, losers, groups, state_dir, source_rows=None) -> dict[str, Path]` | function | Write `07_winners.csv`, `07_duplicates.csv`, `07_groups.csv`. Returns a label-to-path mapping. |

#### Resolve

| Name | Kind | Summary |
|---|---|---|
| `resolve_winners(winners_csv_path, out_path, default_country="bollywood") -> dict` | function | Reconcile every winner CSV row and write `08_resolved.csv`. Returns totals and confidence/country counters. |
| `reconcile(row, default_country="bollywood") -> dict` | function | Merge folder, tag, and filename evidence into one canonical record for a single row. |
| `detect_country(artist, album, default="bollywood") -> str` | function | Heuristic Hollywood/Bollywood classification based on built-in allowlists. |

#### Canonicalize

| Name | Kind | Summary |
|---|---|---|
| `apply_canonical(diff_csv, merged_csv, snapshot_path, undo_script_path, *, dry_run=False, rename=True, include_low=False, source_preference=None, progress=None) -> ApplyResult` | function | Apply the `17_dryrun_diff.csv` to write tags and rename files. Snapshots before every write; undo script updated incrementally. (Renamed from `canonicalize.apply` to avoid shadowing.) |
| `canonicalize_library(cfg, music_root=None, *, progress=None) -> dict[str, int]` | function | Walk the organized music tree, run tiered iTunes → JioSaavn → Shazam lookups per file, write `16_merged.csv`. Returns `{"auto_apply": N, "review": N, "low": N, "no_match": N}`. Excludes the standard internal directories. Per-file errors are reported via `progress` with `error=True` but don't abort the batch. |
| `apply_approvals(merged_csv, approvals_json, snapshot_path, undo_script_path, *, dry_run=False, rename=True, progress=None) -> ApplyResult` | function | Apply user approvals from `19_approvals.json`. Supports `itunes`, `jiosaavn`, `shazam`, `manual`, and `skip` picks. |
| `build_diff(merged_csv_path, dryrun_csv_out, promote_set=None) -> dict` | function | Compute per-row tag diff and write `17_dryrun_diff.csv`. Returns field-action and source counters. |
| `pick_source_for_row(row, preference) -> tuple[dict | None, str]` | function | Return `(fields_dict, source_label)` from a merged-CSV row following the preference order. |

#### Execute

| Name | Kind | Summary |
|---|---|---|
| `execute_plan(plan_csv_path, dups_csv_path, state_dir, mode, dest=None, dry_run=False, *, progress=None) -> dict` | function | Apply `09_plan.csv` (move/copy/symlink) and quarantine duplicates. Returns stats dict. |
| `run_undo(undo_script_path) -> int` | function | Execute a generated `undo_<TS>.sh` bash script. Returns the shell exit code. |

#### Planner

| Name | Kind | Summary |
|---|---|---|
| `plan(resolved_csv_path, out_path, music_root) -> dict` | function | Read `08_resolved.csv`, apply demotion rules, write `09_plan.csv`. Returns totals and country/category counters. |
| `build_destination(row, music_root) -> str` | function | Compute the target absolute path for one resolved row using the country/decade/album scheme. |
| `decade_of(year) -> str` | function | Return `"2010s"`, `"1990s"`, ... or `"Unknown Year"` from a year string. |

### Backup + undo

| Name | Kind | Summary |
|---|---|---|
| `SnapshotStore` | class | SQLite-backed per-file frame snapshots keyed by `fingerprint_sha256`. Survives renames. Excludes large binary frames by default (CORE-14). |
| `DEFAULT_EXCLUDED_FRAMES` | constant | `frozenset` of frame keys excluded from snapshots by default: `APIC`, `covr`, `GEOB`, `PRIV`, `USLT`. Append-only. |
| `generate_undo_script(snapshot_path, ops, undo_script_path) -> Path` | function | Write a thin Python undo script + sibling ops JSON. Script reads snapshot and ops from disk at runtime. |
| `snapshot_tags(paths, snapshot_path) -> Path` | function | Legacy path-keyed JSON snapshot. Prefer `SnapshotStore` for new code. |
| `list_snapshots(state_dir) -> list[dict]` | function | Return metadata for every legacy `tag_snapshot_*.json` in `<state>/backups`. |
| `restore_from_snapshot(snapshot_path) -> dict` | function | Load legacy snapshot + sibling ops JSON, apply tag+rename restore. Returns `{restored, failed, missing_ops}`. |

### Tag I/O

| Name | Kind | Summary |
|---|---|---|
| `read_tags(path) -> Track` | function | Read audio tags via mutagen → ffprobe → mediainfo cascade. `tag_source` records which backend succeeded. (Renamed from `tags.read`.) |
| `write_tags(path, fields) -> None` | function | Write canonical tag fields dispatched by extension: `.mp3` (ID3v2.4), `.m4a` (MP4), `.flac` (Vorbis). (Renamed from `tags.write`.) |
| `snapshot_tags_raw(path) -> dict` | function | Capture every tag frame as a JSON-serialisable dict before a write. (Renamed from `tags.snapshot`.) |

### Cleaning utilities

| Name | Kind | Summary |
|---|---|---|
| `strip_junk(s) -> str` | function | Strip site-junk names, bitrate stamps, and URL fragments from a string. |
| `clean_for_query(s) -> str` | function | Prepare a string for external search APIs: strips parens, feat clauses, extension leaks; preserves version markers. |
| `safe(s, maxlen=120) -> str` | function | Filesystem-safe path component with junk/URL stripping and length cap. |
| `safe_filename(s, maxlen=180) -> str` | function | Filesystem-safe literal filename; lighter than `safe` — no junk stripping. |
| `normalize_key(s) -> str` | function | Lowercase, alpha-only key for dedupe grouping. |
| `normalize_album(album) -> str` | function | Collapse OST naming variants so album siblings group together. |
| `normalize_year(y) -> str` | function | Extract a 4-digit year from a free-form date string. |
| `parse_folder(folder) -> tuple[str, str]` | function | Parse `"Album (YYYY)"` folder name. Returns `(album, year)`. |
| `parse_track_num(filename, tag_track) -> str` | function | Best-effort track number from tag then filename prefix. |
| `title_from_filename(filename) -> str` | function | Derive a clean title from a filename stem (strips track prefix, then `strip_junk`). |
| `strip_year_suffix(album) -> str` | function | Remove a trailing `(YYYY)` suffix from an album string. |
| `junkiness(filename) -> int` | function | Score filename junkiness for dedupe tiebreaking. Higher = junkier. |
| `SITE_JUNK_PATTERNS` | constant | Append-only list of site-junk regex patterns. May grow in minor versions; never shrinks. |

### Approval / review gate

| Name | Kind | Summary |
|---|---|---|
| `ApprovalValidationError` | class | Raised when the user-edited review CSV fails validation. `errors` carries `(row_num, field, issue)` tuples. |
| `build_review_csv(merged_csv, out_path, promote_set=None) -> int` | function | Write the human-review CSV for `review`, `low`, and `no_match` rows. Returns row count. |
| `parse_review_csv(in_path, approvals_out) -> tuple[int, list[dict]]` | function | Strict-validate the edited review CSV and emit `19_approvals.json`. Raises `ApprovalValidationError` on any issue. |
| `batch_rule(merged_csv, approvals_out, rule, *, confidence_min=0.65, buckets=None) -> int` | function | Apply a non-interactive tier-priority rule (e.g. `"jiosaavn>shazam>itunes"`) to unresolved rows. Returns approval count. |
| `summarise_buckets(merged_csv, promote_set=None) -> dict` | function | Return `{decision: count}` for the review queue. Cheap read for status display. |

### Lookup

| Name | Kind | Summary |
|---|---|---|
| `chain(track, cfg) -> tuple[TierMatch | None, str]` | function | Run iTunes → JioSaavn → Shazam in cascade. Short-circuits at first auto-apply confidence. Returns `(best_match, decision)`. |
| `itunes_search(title, artist, album_hint, folder_year, duration_sec, country="us") -> TierMatch | None` | function | iTunes Search API lookup. Returns best-scored `TierMatch` or None. Rate-limits internally (0.8 s/call). |
| `jiosaavn_search(title, artist, album_hint, folder_year, user_agent="Mozilla/5.0") -> TierMatch | None` | function | JioSaavn search API lookup. Best free Bollywood/Punjabi catalog. |
| `shazam_recognize(path) -> TierMatch | None` | function | Synchronous Shazam fingerprint recognition. Raises `ImportError` if `shazamio` is not installed. |
| `score_match(api_title, api_artist, api_album, api_year, api_duration_sec, cur_title, cur_artist, cur_duration_sec, album_hint, folder_year) -> tuple[float, dict]` | function | Score an API result against current tags. Returns `(confidence_in_0_to_1, parts_dict)` with sub-scores and penalties. |
| `score_shazam(api_title, api_artist, cur_title, cur_artist) -> float` | function | Score Shazam's hit against current tags. Includes +0.10 audio-fingerprint trust bonus and version-marker penalty. |
| `decide(score, threshold_auto=0.86, threshold_review=0.65) -> str` | function | Bucket a confidence score into `"auto_apply"` / `"review"` / `"low"`. |
| `extract_apple_music_url(track_or_hub) -> tuple[str, str, str]` | function | Walk Shazam's hub and return `(apple_music_url, adam_id, storefront)`. Prefers song-level URLs. |
| `CircuitBreaker` | class | Filesystem-backed circuit breaker. Trips by writing a marker file after `threshold` consecutive failures. Used to protect all external tiers. |

### Extensions

| Name | Kind | Summary |
|---|---|---|
| `UpgradeExtension` | Protocol | The contract every upgrade extension implements: `preflight()`, `supports()`, `upgrade()`, `cleanup()`. `@runtime_checkable`. |
| `UpgradeCandidate` | dataclass | Frozen read-only record published to extensions. Identity is `fingerprint_sha256`, never path. |
| `UpgradeResult` | dataclass | What an extension reports back for one candidate: status, new path, codec, bit depth, error detail. |
| `UpgradeStatus` | type alias | Literal outcome categories: `upgraded`, `lossy_only_confirmed`, `alac_unservable`, `remix_dj`, `region_blocked`, `transient_error`, `skipped`. |
| `PreflightResult` | dataclass | Health-check record from `UpgradeExtension.preflight()`. Non-healthy result aborts the run. |
| `SUPPORTED_PROTOCOL_VERSIONS` | constant | `frozenset` of supported extension protocol versions (currently `{"1.0"}`). Append-only. |

### Upgrade orchestration

> **Provisional**: `upgrade_one`, `upgrade_batch`, and `permanent_skip_report` are listed here as v0.2 API but are flagged as provisional. They may change shape in v1.0 when `upgrade.py` completes the full `UpgradeExtension` dispatch (see _organizer/LIBRARY_PLAN.md §7 Step 3).

| Name | Kind | Summary |
|---|---|---|
| `upgrade_one` | function | Attempt to upgrade one lossy file to ALAC via the active extension. |
| `upgrade_batch` | function | Batch-upgrade a list of candidates, respecting circuit breakers and `SkipReason` state. |
| `permanent_skip_report` | function | Emit a summary of all permanently-skipped fingerprints and their `SkipReason` values. |

## Name renames at the re-export level

The following names are re-exported under aliases to avoid collisions or improve clarity at the `musicorg.*` namespace level:

| Original | Re-exported as | Reason |
|---|---|---|
| `canonicalize.apply` | `apply_canonical` | `apply` is too generic at the top-level namespace; the rename is self-documenting. |
| `tags.read` | `read_tags` | Avoids collision with Python's built-in `read` idiom at top level. |
| `tags.write` | `write_tags` | Symmetry with `read_tags`. |
| `tags.snapshot` | `snapshot_tags_raw` | Distinguishes from `SnapshotStore`-based snapshots (fingerprint-keyed). |

## What is NOT public API

The following are intentionally NOT exported and may change in any version:

- Anything in `musicorg.cli.*` — CLI is a consumer of the library, not part of it.
- Anything in `musicorg.cli.tui.*` — Textual screens.
- Module-level constants in `musicorg.config`: `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `STATE_ROOT`, `GLOBAL_CONFIG` — XDG defaults for the CLI; embedders should pass `state_root=` / `global_config_path=` explicitly.
- Anything prefixed with `_` in any module.
- `config.resolve_library`, `config.slugify_path`, `config.is_path_excluded`, `config.DEFAULT_EXCLUDE_DIR_NAMES` — useful internal utilities but not yet part of the stable contract.
- `canonicalize.build_new_filename`, `canonicalize.derive_track_num`, `canonicalize.folder_year` — internal canonicalize helpers.
- `scan.ScanStats` — returned by `compute_stats` and `write_tags_csv`; a dependent type but not independently stable yet.
- `resolve.HOLLYWOOD_ARTISTS`, `resolve.HOLLYWOOD_ALBUMS`, `resolve.RESOLVED_FIELDS` — internal routing tables.
- `planner.PUNJABI_HINTS`, `planner.PLAN_FIELDS` — internal routing tables.
- `dedupe.COMPLETENESS_FIELDS`, `dedupe.completeness`, `dedupe.score` — internal dedupe helpers.
- `clean.VERSION_KEYWORDS`, `clean.VERSION_RX`, `clean.JUNK_SUBSTR`, `clean.EXTRA_JUNK`, `clean.UNSAFE_FN_RX`, `clean.FOLDER_RX`, `clean.TRACK_PREFIX_RX`, `clean.YEAR_SUFFIX_RX`, `clean.EXT_LEAK_RX` — internal regex constants.
- `clean.title_version_marker` — internal helper used by lookup tiers.
- Legacy JSON snapshot functions in `backup.py` (`snapshot_tags`, `restore_from_snapshot`, `list_snapshots`) — kept for the CLI, will be superseded by `SnapshotStore` in v1.0. They ARE exported for now to avoid breaking existing code, but new callers should use `SnapshotStore`.
- `lookup.shazam.recognize_async` — async internal entry point; `shazam_recognize` is the public sync wrapper.
- `musicorg.misc`, `musicorg.zip_probe`, `musicorg.refingerprint` — stable internally but not yet part of the public contract.

## Migrating to v1.0

When v1.0 ships:
- The daemon (`musicorg.daemon` + `musicorgd` console_script) will be added (see [_organizer/LIBRARY_PLAN.md](_organizer/LIBRARY_PLAN.md) §7 Step 4).
- The OpenAPI 3.1 schema at `/openapi.json` will become the cross-language stability contract.
- The full `UpgradeExtension` dispatch will replace the current direct gamdl coupling in `upgrade.py`. `upgrade_one` and `upgrade_batch` may change shape.
- `ScanStats` may be promoted to the stable surface once its field set is frozen.
- The legacy JSON snapshot functions (`snapshot_tags`, `list_snapshots`, `restore_from_snapshot`) will be removed. Callers should migrate to `SnapshotStore`.

## Daemon REST route mapping (Wave 4 notes)

When the FastAPI daemon is added, these public functions should map to REST routes:

| Route | Core function |
|---|---|
| `POST /v1/scan` | `scan()` |
| `POST /v1/dedupe` | `group_duplicates()` + `write_dedupe_outputs()` |
| `POST /v1/resolve` | `resolve_winners()` |
| `POST /v1/plan` | `plan()` |
| `POST /v1/canonicalize/diff` | `build_diff()` |
| `POST /v1/canonicalize/apply` | `apply_canonical()` |
| `POST /v1/canonicalize/approvals` | `apply_approvals()` |
| `POST /v1/execute` | `execute_plan()` |
| `POST /v1/upgrade` | `upgrade_batch()` |
| `GET  /v1/jobs/{id}` | Progress via `ProgressEvent` stream |
| `WS   /v1/events` | `ProgressEvent` WebSocket bridge |

## See also

- [_organizer/LIBRARY_PLAN.md](_organizer/LIBRARY_PLAN.md) — full library conversion plan
- [_organizer/optimization.md](_organizer/optimization.md) — pipeline architecture and CORE-* patterns
- [_organizer/APP_PLAN.md](_organizer/APP_PLAN.md) — system architecture
