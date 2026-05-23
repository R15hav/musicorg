# Troubleshooting

---

## Shazam circuit breaker

**Symptom:** musicorg prints a yellow banner saying "Shazam circuit breaker is tripped" and skips the Shazam tier entirely.

**Cause:** 5 or more consecutive Shazam failures were recorded in a single run. The circuit breaker writes a marker file:

```
~/.local/share/musicorg/<name>/SHAZAMIO_UNAVAILABLE.<YYYY-MM-DD>.txt
```

This file persists across runs. Subsequent invocations of `canonicalize`, `refingerprint`, and the wizard skip the Shazam tier without attempting any requests.

**Fix:**

1. Check whether shazamio itself is up to date: `pip install --upgrade shazamio`.
2. Check Shazam's service status.
3. Delete the marker file to re-enable the tier:

```bash
rm ~/.local/share/musicorg/<name>/SHAZAMIO_UNAVAILABLE.*.txt
```

The circuit-breaker threshold is configurable:

```bash
musicorg config set shazam.breaker_threshold 10
```

Source: [`src/musicorg/lookup/breaker.py`](https://github.com/R15hav/musicorg/blob/main/src/musicorg/lookup/breaker.py)

---

## Year-mismatch guardrail overriding the API year

**Symptom:** After `canonical-apply`, some files still have the original year despite the API returning a different year. The `ApplyResult.guarded` counter is non-zero.

**Cause:** The year guardrail in `canonicalize.py` protects the original folder year when a folder is named `Album (YYYY)` and the API returns a year more than 3 years away. This protects against iTunes returning a re-issue or compilation year instead of the original album release year.

**Example:** A folder named `Movie (2014)` and iTunes returning year `2018` triggers the guardrail. The `2014` year is kept.

**Fix:** If the API year is actually correct, override it via the review CSV:

```bash
musicorg --library <name> review --export
# edit 19_review.csv: set the approve column and correct the api_year manually
musicorg --library <name> review --import ~/.local/share/musicorg/<name>/19_review.csv
musicorg --library <name> canonical-apply
```

---

## `~/.local/bin` not on PATH after `pip install --user`

**Symptom:** After `./install.sh --user` or `pip install --user musicorg[cli]`, running `musicorg` gives `command not found`. The same issue affects `gamdl` when installed via `pip install --user gamdl`.

**Cause:** `pip --user` installs binaries to `~/.local/bin`. Many default shell profiles do not include this path.

**Fix:**

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Add this to your `~/.bashrc` or `~/.zshrc` and restart your shell (or run `source ~/.bashrc`).

The `install.sh` script detects and warns about this condition but cannot modify your shell profile automatically.

---

## `gamdl_failed` with rc=-2 (gamdl not on PATH)

**Symptom:** The upgrade phase writes `gamdl_failed` rows to `upgrade_skips.csv` with `returncode: -2` and a `stderr_tail` value containing `gamdl-not-found:`.

**Cause:** `subprocess.run(["gamdl", ...])` raises `FileNotFoundError` because `gamdl` is not on `$PATH`. [`run_gamdl()` at `upgrade.py#L191`](https://github.com/R15hav/musicorg/blob/main/src/musicorg/upgrade.py#L191) catches `FileNotFoundError` and converts it to return code `-2` with a `stderr` string of `gamdl-not-found: <exc>`.

**Root cause:** `pip install --user gamdl` places the binary at `~/.local/bin/gamdl`. Many default shell profiles do not include `~/.local/bin` on `$PATH`.

**Fix:**

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Add this to `~/.bashrc` or `~/.zshrc`, then reload your shell (`source ~/.bashrc`) and re-run Stage 3. The upgrade phase is resumable — candidates already recorded as `upgraded` in `upgrade_skips.csv` will not be re-fetched.

See also: [~/.local/bin not on PATH after `pip install --user`](#localbin-not-on-path-after-pip-install-user).

---

## Dup-quarantine path falls back to a sibling `_duplicates/` folder

**Symptom:** Duplicate losers appear in `<source-parent>/_duplicates/` instead of `<library_root>/Music/_duplicates/`.

**Cause:** The quarantine logic looks for a `Music` ancestor directory to root `_duplicates/` under. If no `Music` ancestor is found (e.g., the library root is `/home/user/MyTunes/` with no `Music/` subdirectory), it falls back to `<source-file-parent>/_duplicates/`.

**Fix:** Run `apply` before `quarantine`. The `apply` command creates `<library_root>/Music/` as part of the organized tree, giving the quarantine logic a stable anchor point.

Alternatively, run Stage 1 in full (scan → dedupe → resolve → plan → apply) via the wizard, which ensures the Music tree exists before quarantine happens.

---

## `audioTraits: lossless` but ffprobe sees AAC

**Symptom:** A track shows `alac_listed_but_not_servable` in the permanent-skip report, even though the Apple Music page shows it as lossless.

**Cause:** Apple's `audioTraits` field in the API response claims lossless availability for some tracks that are actually only served as AAC. musicorg's upgrade pipeline calls ffprobe on every gamdl output file before replacing the original. If ffprobe reports an AAC codec, the file is discarded and the track is permanently skipped.

This is intentional. In the original production run, 26 out of ~580 files exhibited this mismatch. Accepting a 256 kbps AAC file as a "lossless upgrade" would be worse than keeping the original.

**No user action needed.** The `alac_listed_but_not_servable` entry in `upgrade_skips.csv` is the correct outcome. The original lossy file is untouched.

---

## gamdl idempotency trap

**Symptom:** Running `upgrade` a second time produces no new ALAC files, even for candidates that failed the first time due to a transient error (network timeout, service hiccup).

**Cause (if not using musicorg's upgrade command directly):** gamdl exits 0 without re-downloading if its output file already exists in the staging directory. If you pointed multiple upgrade runs at the same staging directory, gamdl silently reuses stale output.

**Why this does not happen with `musicorg upgrade`:** musicorg generates a unique staging subdirectory for every invocation (`_upgrade_staging/run_YYYY-MM-DD_HHMMSS/`). Each run starts with an empty staging area, so gamdl always downloads fresh. The per-run directory also makes it easy to inspect what a specific run produced.

**If you bypassed musicorg and called gamdl directly:** Delete the stale staging output or use a new `--output-path` for each run.

---

## Wizard finds no audio files

**Symptom:** After entering a music folder, the wizard prints `no audio files found` and exits.

**Cause:** Either the path is wrong, the folder is empty, or all files are in subdirectory names that match the default exclude list (`_duplicates`, `_misc`, `_replaced`, `_upgrade_staging`, `_organizer`).

**Fix:**

1. Confirm the path exists and contains `.mp3`, `.m4a`, `.flac`, or similar audio files.
2. If your files are inside a directory named one of the above, rename the parent folder or use `--exclude` to be explicit about what to skip.
3. Run `musicorg --library <name> scan <path>` directly to see the scanner's output before running the wizard.
