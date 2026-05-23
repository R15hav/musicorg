# ALAC Upgrade (Stage 3)

Stage 3 replaces lossy files (MP3, AAC, M4A) with ALAC (Apple Lossless) downloads from Apple Music using [gamdl](https://github.com/glomatico/gamdl). It is entirely optional and defaults to off in the wizard.

Source: [`src/musicorg/upgrade.py`](https://github.com/R15hav/musicorg/blob/main/src/musicorg/upgrade.py)

---

## Prerequisites

You need:

- An active Apple Music subscription.
- `cookies.txt` — your Apple Music session cookies, exported from a browser session. gamdl's own documentation explains how to export these; the process is the same as for other Apple Music downloader tools.
- A Widevine device file (`.wvd`) — required by gamdl for DRM decryption. See [gamdl's README](https://github.com/glomatico/gamdl) for how to obtain one.
- `gamdl` on your PATH: `pip install gamdl` (or let the wizard install it).
- `shazamio` installed (for the refingerprint pass): `pip install shazamio`.

!!! warning "Subscription required"
    gamdl downloads from Apple Music and requires a paid subscription. musicorg does not circumvent DRM — it uses gamdl as an external tool that handles authentication.

---

## The upgrade pipeline

Stage 3 consists of three steps, each of which can be run independently via the CLI:

### Step 1 — Shazam refingerprint pass

```bash
musicorg --library <name> refingerprint
```

Runs Shazam audio fingerprinting against every lossy file in the organised tree. For each match, Shazam returns an Apple Music URL (`apple_music_url`), Adam ID, and storefront. These are written to `30_shazam_refingerprint.csv`. The pass waits 1.5 seconds between requests by default to avoid rate-limiting; this is configurable via `cfg.shazam_inter_request_sleep_sec`.

This pass is what connects a local lossy file to the correct Apple Music track, independently of whatever metadata the file currently has. It is the most reliable source of Apple Music URLs because it identifies tracks by audio content rather than tags.

The `upgrade` command reads `30_shazam_refingerprint.csv` first; if it does not exist, it falls back to `16_merged.csv` (Shazam results collected during the canonicalize phase).

### Step 2 — gamdl download and ffprobe verification

```bash
musicorg --library <name> upgrade --cookies ./cookies.txt --wvd ./device.wvd
```

For each candidate with a resolved Apple Music URL:

1. gamdl is invoked as a subprocess with the URL, cookies, and Widevine device.
2. Output goes to a **per-run unique staging directory** (`_upgrade_staging/run_YYYY-MM-DD_HHMMSS/`). A fresh subdirectory is created for every invocation of `upgrade_batch`. This defeats gamdl's idempotency trap: gamdl exits 0 without re-downloading if its output file already exists from a previous run. With per-run dirs, each run always starts fresh.
3. After gamdl exits, the staging directory is scanned for new `.m4a` or `.flac` files.
4. **ffprobe verification** — every new file is checked with ffprobe before the original is replaced. Apple's `audioTraits: lossless` flag is never trusted on its own. In the reference production run, 26 files had `audioTraits: lossless` but ffprobe reported an AAC stream — not ALAC. Any file that fails the ffprobe check is recorded as `alac_listed_but_not_servable` and skipped permanently.
5. If the download is a genuine ALAC, the original lossy file is moved to `_replaced/` and the new ALAC takes its place.

### Step 3 — permanent-skip report

```bash
musicorg --library <name> permanent-skip-report
```

Shows a summary of all permanently-skipped files and their reasons.

---

## Permanent-skip taxonomy

Permanent skips are written to `<state>/upgrade_skips.csv`. Once a fingerprint is permanently skipped, the upgrade loop will not attempt it again.

| Reason | Meaning |
|---|---|
| `lossy_only_on_apple_music` | gamdl confirmed ALAC is unavailable for this track on Apple Music |
| `alac_listed_but_not_servable` | Apple's `audioTraits` claimed lossless, but ffprobe confirmed the download is AAC |
| `remix_dj_not_on_apple` | The track is a remix or DJ set absent from Apple's catalog |
| `wrong_match_permanent` | The Apple Music URL points to a different recording than the local file |
| `shazam_no_match` | Shazam audio fingerprinting returned no match for this file |

These values come from `upgrade.py::PERMANENT_SKIP_REASONS` and are persisted directly to CSV.

!!! tip
    `alac_listed_but_not_servable` is the most surprising permanent skip. It means Apple's metadata claimed ALAC availability but the actual download stream was AAC. The ffprobe check catches this before any file is overwritten.

---

## Rolling back an upgrade

Each upgrade run generates an undo script:

```bash
ls ~/.local/share/musicorg/<name>/undo_upgrade_*.py
python3 undo_upgrade_<TS>.py
```

The undo script is self-contained: the ops list is inlined directly into the generated `.py` file via `json.dumps(...)` ([`upgrade.py#L348`](https://github.com/R15hav/musicorg/blob/main/src/musicorg/upgrade.py#L348)). There is no sibling JSON file. Each entry in the ops list contains the keys `new_path`, `replaced_path`, and `original_path`. The script moves the original lossy files back from `_replaced/` and removes the ALAC files.

---

## Recovering from interrupted runs

If a run is interrupted (Ctrl+C, crash, out of disk), ALAC files may remain in the staging directory without their originals having been replaced:

```bash
musicorg --library <name> recover-staging
```

This scans `_upgrade_staging/` for valid ALAC files not yet moved to their destinations, verifies them with ffprobe, and completes the move.

---

## gamdl installation note

The installer and wizard offer to install gamdl via `pip install --user gamdl`. If installed this way, the `gamdl` binary lands at `~/.local/bin/gamdl`. If `~/.local/bin` is not on your `PATH`, musicorg cannot invoke it. See [Troubleshooting — `~/.local/bin` not on PATH after `pip install --user`](troubleshooting.md#localbin-not-on-path-after-pip-install-user).
