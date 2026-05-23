# musicorg

A Linux terminal app that organizes a messy music library end to end: walks your music folder, dedupes near-identical files, cleans junk-laden tags via iTunes / JioSaavn / Shazam, and optionally upgrades lossy tracks to ALAC via [gamdl](https://github.com/glomatico/gamdl). Every phase generates an undo script.

Built from a 34-script production pipeline that organized a 580-file mixed Bollywood / Hollywood / Punjabi corpus. The library code lifts that pipeline into a shared core (`musicorg/`); on top of that sit a guided wizard, a Typer CLI for power users, and Textual TUIs for review/approval flows.

---

## Install

```bash
git clone <this repo>
cd music_upgrader
./install.sh                # interactive — asks before each piece
```

Flags:

```bash
./install.sh --full         # everything non-interactive (system + Shazam + gamdl)
./install.sh --base         # CLI only, no Shazam, no gamdl
./install.sh --user         # install into ~/.local instead of .venv/
./install.sh --uninstall    # remove the app (leaves config + state)
./install.sh --skip-system  # don't touch system packages
```

The installer:
1. Detects your distro (apt / dnf / pacman / zypper / apk) and installs `ffmpeg`, `mediainfo`, `xdg-utils`.
2. Creates a venv at `.venv/` (falls back to `--user` if `python3-venv` isn't installable).
3. Installs `musicorg` editable, plus the optional `[shazam]` and `gamdl` extras on consent.
4. Verifies that the `musicorg` command launches.

---

## Run it

```bash
musicorg
```

The bare command launches the guided wizard:

1. **Stage 1 — Organize file tree.** Asks for music folder, library name, default country, move/copy/symlink mode. Then scans → dedupes → resolves → plans → previews → applies. Generates an undo `.sh`.
2. **Stage 2 — Canonical metadata.** Optionally installs `shazamio`. Runs tiered iTunes → JioSaavn → Shazam lookup. For unresolved rows asks: batch-approve by priority rule, open a CSV in `$EDITOR`, or skip. Writes tags + renames with a thin undo `.py`.
3. **Stage 3 — Lossless upgrade (optional).** Installs `gamdl` if absent. Prompts for `cookies.txt` and `.wvd` device file. Runs Shazam refingerprint to harvest Apple Music URLs, then drives gamdl per track. Surfaces a permanent-skip report at the end.

Every stage is opt-out. Interrupt with Ctrl+C any time — state is checkpointed; the next run picks up where you left off.

---

## Try it now (sample library)

A small fixture is included that exercises every code path: Bollywood album folders with `(YYYY)` naming, plain folders that get demoted to singles, site-junk in filenames (`[Songs.PK]`, `(www.PagalWorld.com)`), duplicates across folders, a Hollywood artist (Linkin Park) for the allowlist routing, a Punjabi single (Guru Randhawa), and a garbage-named file with no tags (`Track 06.mp3`). Plus non-audio junk for `misc-sweep` to find.

```bash
# Build (or rebuild) the fixture
python3 tests/fixtures/build_fixture.py
ls tests/fixtures/library-small/

# Run the wizard against it
musicorg
#   → music folder: tests/fixtures/library-small
#   → library name: demo
#   → default country: bollywood
#   → mode: move
#   → say "y" to Stage 1, "n" to Stage 2 and 3 for the dry test
```

Expected outcome (Stage 1 only):

```
tests/fixtures/library-small/Music/
├── Bollywood/2010s/
│   ├── Ek Villain (2014)/
│   │   ├── 01 - Galliyan.mp3            # site junk stripped
│   │   ├── 02 - Hamdard.mp3             # dup winner from RISHAV/
│   │   └── 03 - Banjaara.mp3            # PagalWorld junk stripped
│   ├── Jab Tak Hai Jaan (2012)/01..03 - *.mp3
│   └── Single (2014)/01..02 - *.mp3
├── Hollywood/2010s/Linkin Park/Living Things (2012)/01..02 - *.mp3
├── Singles/Bollywood/
│   ├── Shraddha Kapoor/Galliyan (Unplugged).mp3   # version variant preserved
│   └── Unknown Artist/Track 06.mp3                # tag-less fallback
└── _duplicates/.../RISHAV/05-Hamdard.mp3          # loser preserved by path
```

Then to roll the whole thing back:

```bash
musicorg --library demo undo
```

---

## Power-user CLI

The wizard is just a thin orchestrator around individual commands. Every phase is also a standalone subcommand:

```bash
musicorg --library home scan ~/Music
musicorg --library home dedupe                                 # or --interactive for TUI
musicorg --library home resolve
musicorg --library home plan
musicorg --library home apply --dry-run
musicorg --library home apply --mode move                      # commit; generates undo_<TS>.sh
musicorg --library home undo

musicorg --library home canonicalize                           # tiered iTunes → JioSaavn → Shazam
musicorg --library home review --export                        # write a review CSV
$EDITOR ~/.local/share/musicorg/home/19_review.csv             # fill the approve column
musicorg --library home review --import ~/.local/share/musicorg/home/19_review.csv
musicorg --library home approve --rule "jiosaavn>shazam>itunes" # batch alternative
musicorg --library home canonical-apply --dry-run
musicorg --library home canonical-apply
musicorg --library home canonical-undo --latest

musicorg --library home refingerprint                          # Shazam pass + harvest Apple Music URLs
musicorg --library home upgrade --cookies ./cookies.txt --wvd ./device.wvd
musicorg --library home recover-staging                        # rescue orphans from failed gamdl runs
musicorg --library home permanent-skip-report

# Textual TUIs
musicorg --library home review --interactive                   # canonical CSV-edit-reimport flow
musicorg --library home dedupe --interactive                   # two-pane dup picker
musicorg --library home fill                                   # per-row card resolver

# Config
musicorg config init
musicorg config set acoustid.api_key XXXX
musicorg config show
```

Run `musicorg --help` for the full list (~30 commands).

---

## State + output layout

For a library at `~/Music` (slug `home`), state lives at `~/.local/share/musicorg/home/`:

```
~/.local/share/musicorg/home/
├── config.ini                                       # per-library overrides
├── 01_tags.csv                                      # scan
├── 07_winners.csv, 07_duplicates.csv, 07_groups.csv # dedupe
├── 08_resolved.csv                                  # resolve
├── 09_plan.csv                                      # plan
├── 16_merged.csv                                    # canonicalize (merged tier view)
├── 17_dryrun_diff.csv                               # canonical-apply --dry-run
├── 19_review.csv, 19_approvals.json                 # user-approval round-trip
├── 30_shazam_refingerprint.csv                      # refingerprint
├── upgrade_skips.csv                                # permanent-skip taxonomy
├── backups/tag_snapshot_<TS>.json                   # tag snapshots
├── logs/<phase>.log
└── undo_<TS>.sh, undo_phase18_<TS>.py, undo_upgrade_<TS>.py
```

Organized output (after `apply`):

```
<library_root>/Music/
├── Bollywood/<decade>/<movie (year)>/NN - Track.mp3
├── Hollywood/<decade>/<artist>/<album (year)>/NN - Track.mp3
├── Singles/{Bollywood,Punjabi,Hollywood}/<artist>/Track.mp3
├── _duplicates/<original-subpath>/                  # dup losers preserved
├── _misc/                                            # non-audio sweep target
├── _replaced/                                        # original lossy after ALAC upgrade
└── _upgrade_staging/<run-id>/                        # per-run gamdl staging
```

Global config: `~/.config/musicorg/config.ini`.

---

## Safety

- **Every phase generates an undo.** File-move phases emit `undo_<TS>.sh`. Tag-write phases emit a thin `undo_phase*.py` that reads a separate JSON snapshot at runtime — the snapshot is never inlined (production runs produced 41 MB scripts that way).
- **Year-mismatch guardrail.** If a folder is named `Movie (YYYY)` and an API returns a year ≥ 3 years away, the folder year is kept (catches iTunes-returns-compilation cases).
- **Circuit breaker for Shazam.** 5 consecutive `shazamio` failures writes `SHAZAMIO_UNAVAILABLE.<date>.txt`; subsequent runs skip the tier with a banner. Delete the marker to retry.
- **gamdl idempotency trap mitigation.** Each upgrade run uses a unique staging subdir so gamdl re-downloads instead of silently no-op'ing.
- **`audioTraits` is never trusted.** Every gamdl output is ffprobe'd; tracks claimed lossless but served as AAC are marked `alac_listed_but_not_servable`.
- **Collision handling.** Filename collisions get ` (2)`, ` (3)` suffixes, logged to `11_collisions.csv`.

---

## Permanent skip taxonomy

```
lossy_only_on_apple_music         gamdl confirms ALAC unavailable
alac_listed_but_not_servable      audioTraits claims lossless but ffprobe sees AAC
remix_dj_not_on_apple             remix/mashup absent from Apple's catalog
wrong_match_permanent             Apple URL points to a different recording
shazam_no_match                   audio fingerprint failed
```

`musicorg permanent-skip-report` shows counts + paths.

---

## What's where

```
src/musicorg/                   library — pure Python, no CLI deps
├── clean.py                    junk regex, query prep, version-marker logic
├── tags.py                     mutagen → ffprobe → mediainfo cascade + writers
├── identity.py                 audio-stream sha256 (content-addressed join key)
├── models.py                   dataclasses (Track, ResolvedTrack, TierMatch, ApplyResult, ProgressEvent, SkipReason)
├── config.py                   XDG-Linux config + per-library state dirs
├── scan.py                     walk + per-file tag read
├── dedupe.py                   group + score winners
├── resolve.py                  folder/tag/filename reconciliation + country heuristics
├── planner.py                  destination tree building + album-counts demotion
├── executor.py                 move/copy/symlink + collision handling + undo .sh
├── misc.py                     non-audio sweep
├── zip_probe.py                detect zip backups of already-organized folders
├── canonicalize.py             diff + apply + guardrails
├── backup.py                   snapshot + thin undo script generator
├── approval.py                 CSV round-trip + batch rule
├── upgrade.py                  upgrade orchestration + ffprobe verification + skip taxonomy
├── refingerprint.py            Shazam pass + orphan recovery
├── extensions/                 plugin protocol for third-party upgraders (gamdl, ...)
│   └── protocol.py             UpgradeExtension, UpgradeCandidate, UpgradeResult, PreflightResult
└── lookup/
    ├── itunes.py, jiosaavn.py, shazam.py
    ├── scoring.py              title × 0.55, artist × 0.25, duration × 0.20, +bonuses/-penalties
    ├── breaker.py              circuit breaker for unofficial APIs
    └── __init__.py             chain() orchestrator

src/musicorg_cli/               reference CLI consumer — Typer + Textual + Rich
├── main.py                     Typer entry — 30 subcommands
├── wizard.py                   guided end-to-end wizard
└── tui/                        review_app, fill_app, dup_review_app, canonical_app

tests/fixtures/build_fixture.py regenerates the demo library

examples/                       embedding patterns
├── 01_basic_scan.py
├── 02_progress_callback.py
├── 03_full_pipeline.py
├── 04_custom_extension.py
├── 05_embed_in_fastapi.py
└── 06_embed_in_pyside.py

PUBLIC_API.md                   library API contract (SemVer after v1.0)
install.sh                      distro-aware installer
```

The library (`musicorg`) is pure-Python with only `mutagen` + `requests`. The CLI (`musicorg_cli`) adds Typer/Textual/Rich and is installed via the `[cli]` extra. Embedders use the library directly — see `examples/` and `PUBLIC_API.md`.
```

---

## Uninstall

```bash
./install.sh --uninstall    # removes the venv / user install
rm -rf ~/.config/musicorg ~/.local/share/musicorg   # optional: nukes config + library state
```

---

## License

MIT.
