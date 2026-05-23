# Wizard

The wizard is the recommended first-run experience. Run `musicorg` with no arguments (or `musicorg organize`) to start it. It walks your library through up to three stages, confirming before each one and generating an undo script at each step.

You can interrupt with Ctrl+C at any point. State is checkpointed between stages, so the next run picks up where you left off.

Source: [`src/musicorg_cli/wizard.py`](https://github.com/R15hav/musicorg/blob/main/src/musicorg_cli/wizard.py)

---

## Setup prompts

Before any stage runs, the wizard asks four questions:

**1. Music folder to organize**

```
Music folder to organize [/current/working/directory]:
```

Enter the absolute path to the folder you want to organise. The wizard resolves `~` and relative paths. Defaults to the current working directory.

**2. Library name**

```
Library name (used for state dir) [<derived-from-path>]:
```

A short slug used to name the state directory under `~/.local/share/musicorg/<name>/`. The default is the **first hyphen-segment** of the slugified path — for example, `/home/user/Music` slugifies to `home-user-music-abc123` and the offered default is `home`. Using a memorable name (e.g. `home`, `nas`, `external`) makes it easier to reference the library later with `musicorg --library home <command>`.

**3. Default country for unknown tracks**

```
Default country for unknown tracks [bollywood]:
```

Choices: `bollywood`, `hollywood`, `unknown`. Controls where tracks with no identifiable country end up in the destination tree. Can be changed per-library in config later.

**4. How should files be placed?**

```
How should files be placed? [move]:
```

Choices: `move`, `copy`, `symlink`. `move` is the default and the most common choice. `copy` requires a `--dest` path (not prompted in the wizard; use the CLI for this). `symlink` is useful for testing.

After these four prompts the wizard writes `config.ini` under the library state directory and shows a summary panel before proceeding.

---

## Stage 1 — Scan and organize file tree

**Confirmation prompt:**

```
Stage 1 — Scan & organize file tree? [Y/n]:
```

When confirmed, the wizard runs these phases in sequence without further prompts:

If Stage 1 scan finds 0 audio files, the wizard prints `no audio files found — nothing to do` and exits the entire wizard, not just Stage 1.

### 1a. Scan

Walks the library root, reads audio tags via mutagen → ffprobe → mediainfo cascade, and computes a SHA-256 audio-stream fingerprint per file. Writes `01_tags.csv`.

Output line:
```
scan: 42 files (mutagen=38, ffprobe=3, mediainfo=1, unreadable=0)
```

### 1b. Dedupe

Groups files by normalised `title | artist` + duration bucket. Picks winners (higher bitrate, lower junk score). Writes `07_winners.csv`, `07_duplicates.csv`, `07_groups.csv`.

Output line:
```
dedupe: 2 duplicate groups, 40 winners, 4 losers
```

### 1c. Resolve

Merges folder name, embedded tags, and filename evidence into one canonical record per winner. Applies country heuristics. Writes `08_resolved.csv`.

Output line:
```
resolve: confidence={'high': 32, 'medium': 6, 'low': 2}, country={'bollywood': 34, 'hollywood': 6}
```

### 1d. Plan

Computes target destination paths under `<library_root>/Music/<country>/<decade>/<album (year)>/`. Albums with only one track are demoted to `Singles/`. Writes `09_plan.csv`. Displays a preview table of the first 10 rows.

Output line:
```
plan: 40 files routed; by_country={'bollywood': 34, 'hollywood': 6}
```

### 1e. Apply confirmation

```
Apply this plan (move)? [Y/n]:
```

Answering `n` leaves `09_plan.csv` in place for later and skips the actual file moves. Answering `y` executes the plan (move/copy/symlink), quarantines dup losers to `<library_root>/Music/_duplicates/`, and writes `undo_<TS>.sh`.

If you skip apply here, you can run it later with:

```bash
musicorg --library <name> apply --mode move
```

---

## Stage 2 — Canonical metadata

**Confirmation prompt:**

```
Stage 2 — Clean up metadata via iTunes/JioSaavn/Shazam? [Y/n]:
```

!!! note
    Stage 2 reads from `<library_root>/Music/`, so Stage 1 must have completed at least once (or you must have run `musicorg apply` previously).

### 2a. Shazam opt-in

```
Include Shazam audio fingerprinting? (slower; needs `shazamio`) [Y/n]:
```

If `shazamio` is not installed and you answer `y`, the wizard offers to install it:

```
shazamio is not installed.
Install shazamio now? [Y/n]:
```

If the install fails or you decline, the wizard continues with iTunes + JioSaavn only.

### 2b. Lookup

Runs the tier chain (iTunes → JioSaavn → [Shazam]) against every audio file in the organized tree. Results are bucketed into `auto_apply` (confidence ≥ 0.86), `review` (≥ 0.65), `low` (< 0.65), and `no_match`. Writes `16_merged.csv`.

Progress bar shows file count. On completion:

```
╭──────────────────────────╮
│ canonicalize buckets     │
│ auto_apply    28         │
│ review         8         │
│ low            3         │
│ no_match       1         │
╰──────────────────────────╯
```

### 2c. Approval for unresolved rows

If `review` + `low` count is > 0:

```
12 files need approval (review + low buckets)
How to handle them? [batch/edit/skip] (batch):
```

- **batch** — apply a tier-priority rule automatically. You are then prompted for the rule:
  ```
  Priority rule [jiosaavn>shazam>itunes]:
  ```
  Any rule of the form `tier>tier>...` works. The wizard defaults to `jiosaavn>shazam>itunes` when Shazam is in the tier list, otherwise `jiosaavn>itunes`.

- **edit** — the wizard first shows:
  ```
  Press enter to launch the editor []:
  ```
  After you press Enter, it opens `19_review.csv` in your `$VISUAL` / `$EDITOR` (falls back to `nano`, `vim`, `vi`). Edit the `approve` column to one of: `itunes`, `jiosaavn`, `shazam`, `manual`, or `skip`. Save and close the editor; the wizard validates and writes `19_approvals.json`.

- **skip** — only `auto_apply` rows are applied in the next step.

### 2d. Write tags confirmation

```
Write tags + rename files now? [Y/n]:
```

Applies `auto_apply` rows and any recorded approvals. Takes a tag snapshot first (`backups/tag_snapshot_<TS>.json`), writes tags, renames files, and generates `undo_phase18_<TS>.py` (and optionally `undo_phase20_<TS>.py` for approval rows).

The undo script reads the snapshot from disk at runtime — it is a thin Python script, never an inlined blob.

---

## Stage 3 — ALAC upgrade (optional)

**Confirmation prompt:**

```
Stage 3 — Upgrade lossy tracks to ALAC via gamdl? [y/N]:
```

This stage defaults to `n`. It requires an Apple Music subscription, `cookies.txt` exported from your browser, and a Widevine device file (`.wvd`). See [upgrade.md](upgrade.md) for how to obtain these.

### 3a. shazamio check

The refingerprint pass that harvests Apple Music URLs requires `shazamio`. The wizard checks and offers to install it if missing.

!!! warning
    If you decline the shazamio install, the wizard prints `Shazam needed for the refingerprint pass — skipping upgrade` and exits Stage 3 entirely. You will **not** be prompted for gamdl, cookies, or WVD paths.

### 3b. gamdl check

```
gamdl not on PATH
Install gamdl now? [Y/n]:
```

### 3c. Credentials

```
Path to cookies.txt (blank to skip) []:
Path to your .wvd device file (blank to skip) []:
```

Enter the paths to your `cookies.txt` and `.wvd` files. Leave blank to skip (you can pass them later via `musicorg --library <name> upgrade --cookies ./cookies.txt --wvd ./device.wvd`).

### 3d. Shazam refingerprint pass

```
Run Shazam refingerprint pass first? (harvests Apple Music URLs) [Y/n]:
```

Runs a fresh Shazam audio-fingerprint pass against all lossy files in the organized tree. Harvests Apple Music URLs and writes `30_shazam_refingerprint.csv`. This is the primary source of URLs for the upgrade step.

The upgrade step reads candidates from `<state>/30_shazam_refingerprint.csv` if that file exists, otherwise it falls back to `<state>/16_merged.csv`.

### 3e. Upgrade

```
upgrade candidates: 25
Run gamdl upgrade now? [Y/n]:
```

Drives `gamdl` per track. Each run uses a unique staging directory (`_upgrade_staging/<run-id>/`) so gamdl cannot silently skip re-downloads. Every download is verified with ffprobe before the staged file replaces the original. Writes `upgrade_skips.csv` for permanently-skipped tracks.

On completion, a permanent-skip summary table is shown.

### Stage 3 notes — Shazam circuit breaker

After 5 consecutive shazamio failures, the wizard writes a marker file:

```
<state_dir>/SHAZAMIO_UNAVAILABLE.<YYYY-MM-DD>.txt
```

On subsequent runs, the wizard detects the marker and prints:

```
Shazam circuit breaker tripped (<filename>); skipping refingerprint
```

The refingerprint pass is skipped for that run. To retry, delete the marker manually:

```bash
rm ~/.local/share/musicorg/<slug>/SHAZAMIO_UNAVAILABLE.*.txt
```

See [troubleshooting.md](troubleshooting.md#shazam-circuit-breaker) for diagnosis steps.

---

## Summary

After all stages complete (or are skipped), the wizard prints a summary panel:

```
done.
State + logs:   ~/.local/share/musicorg/<name>/
Undo scripts:   ~/.local/share/musicorg/<name>/undo_*.sh, undo_phase*.py, undo_upgrade*.py
Snapshots:      ~/.local/share/musicorg/<name>/backups/

To roll back:
  musicorg --library <name> undo                    # file moves
  musicorg --library <name> canonical-undo --latest # tag writes
```
