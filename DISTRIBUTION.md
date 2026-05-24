# musicorg distribution plan

How the app reaches users. Opinionated, tiered, with concrete next steps.

## Constraints that shape this plan

1. **Linux-only.** No macOS or Windows targets (yet). Filesystem layout assumes POSIX + XDG.
2. **System tools required: `ffmpeg`, `mediainfo`, `xdg-utils`.** These are 100+ MB combined and hard to bundle reliably across distros. The right move is to pull them via the host package manager, not ship them.
3. **Terminal app.** No graphical assets, no `.desktop` file required (yet).
4. **Two-package layout.** `musicorg` (library, pure Python) + `musicorg_cli` (Typer/Textual/Rich). Distribution channels need to install both via the `[cli]` extra.
5. **Optional extras: `shazamio`** (heavy: aiohttp + pydub + numpy + Rust fingerprinter) and **`gamdl`** (needs Apple Music subscription + Widevine device file). Both must be opt-in.
6. **Audience.** Two cohorts:
   - **Python-aware** (uses `pip`/`pipx`/`uv` already) — wants `pip install musicorg`.
   - **End-user Linux desktop** — wants `curl … | sh` or "find it in GNOME Software".

## Tier 1 — do these now (free, low maintenance)

These three channels cover ~90% of the realistic audience with the work already mostly done.

### 1.1 PyPI

Publish `musicorg` to PyPI. The library is already structured correctly: `pyproject.toml` is PEP 621, the package builds as a wheel today.

**Install paths users get:**
```bash
pip install musicorg              # library only — for embedders
pip install musicorg[cli]         # library + Typer/Textual/Rich CLI
pip install musicorg[cli,shazam]  # + shazamio
pipx install 'musicorg[cli]'      # recommended for end users
uv tool install 'musicorg[cli]'   # modern equivalent
```

**Trade-offs.** PyPI doesn't install `ffmpeg`/`mediainfo` — users hit a runtime error if missing. The CLI's first-run check should detect this and print install instructions per distro (already partially done in `install.sh`'s system-deps step; pull that logic into the wizard as a preflight).

**Action items:**
- [ ] Reserve the `musicorg` name on PyPI (check availability — may need a suffix like `musicorg-cli` if taken).
- [ ] Add a GitHub Actions workflow (`.github/workflows/publish.yml`) that builds the wheel + sdist on tag push and uploads via Trusted Publishing (no API token in secrets).
- [ ] Add a `preflight` check in `musicorg_cli/wizard.py` that warns when `ffprobe`/`mediainfo` aren't on PATH.
- [ ] Add a `MANIFEST.in` if any non-code data files (currently none).
- [ ] Tag `v0.2.0` and ship.

### 1.2 GitHub Releases + `curl | sh` installer

`install.sh` already does the right thing: detects the distro, pulls system deps, creates a venv, installs from a local clone, launches the wizard. Repurpose it for a remote install.

**Install path users get:**
```bash
curl -fsSL https://raw.githubusercontent.com/R15hav/musicorg/main/install.sh | bash
# or to install without auto-launch:
curl -fsSL https://raw.githubusercontent.com/R15hav/musicorg/main/install.sh | bash -s -- --no-run
```

**Trade-offs.** The `curl | sh` pattern is controversial (security purists hate it) but the de-facto standard for cross-distro CLI tools (rustup, deno, uv, ollama). The script is human-readable; users who care can `curl ... | less` first.

**Action items:**
- [ ] Refactor `install.sh` to clone the repo into `$HOME/.local/share/musicorg-src/` when run via `curl | sh` (currently requires running from inside the repo).
- [ ] Add a `--version` flag so users can pin: `bash install.sh --version v0.2.0`.
- [ ] Publish each release with a GitHub Release containing a signed checksum (`install.sh.sha256`).
- [ ] Add the curl one-liner to the README right under the install header.

### 1.3 GitHub Releases — GUI AppImage (Linux x86_64)

The desktop GUI (`musicorg-gui`) ships as a single-file AppImage attached to the same GitHub Release as the library. **It is never published to PyPI.** `pip install musicorg` is and stays library-only — the GUI has Qt6 / PySide6 + bundled Python and does not belong in the wheel ecosystem.

**Install path:**
```bash
# from the GitHub Release page, or:
curl -fsSL -o musicorg-gui.AppImage \
    https://github.com/R15hav/musicorg/releases/download/vX.Y.Z/musicorg-gui-vX.Y.Z-x86_64.AppImage
chmod +x musicorg-gui.AppImage
./musicorg-gui.AppImage
```

**Platform order (locked):** Linux AppImage is the **first** GUI distribution target. Windows `.exe` and macOS `.app` follow in that order — not in parallel, not earlier.

**How it's produced.** PyInstaller assembles a one-folder bundle from `packaging/musicorg-gui.spec` (entry point `packaging/launcher.py`), which is then dropped into an AppDir built from `packaging/AppDir-template/` (AppRun, `.desktop`, SVG icon) and sealed by `appimagetool`. The full pipeline is `scripts/build_appimage.sh`, runnable locally or from CI; the GitHub Actions workflow is `.github/workflows/release-appimage.yml` (tag push → release asset; `workflow_dispatch` → artefact only).

**`[gui-dev]` is a build-time extra, not a public install path.** It pulls PySide6 + pyinstaller for developers building the AppImage from source. Users are never expected to `pip install 'musicorg[gui-dev]'`.

**Trade-offs.**
- Bundle size is ~60 MB (PySide6 dominates). Acceptable for a desktop app; not great as a pipx-style install.
- ffmpeg / mediainfo are still host dependencies — same story as the CLI. The first-run check needs to surface install hints from inside the GUI too (not just the CLI wizard).
- AppImage assumes glibc; very old distros won't run it. The CI builder runs on `ubuntu-latest`, which sets the floor.

**Action items:**
- [x] PyInstaller spec + launcher (`packaging/musicorg-gui.spec`, `packaging/launcher.py`).
- [x] AppDir template (`packaging/AppDir-template/`).
- [x] Local build pipeline (`scripts/build_appimage.sh`).
- [x] GitHub Actions release workflow (`.github/workflows/release-appimage.yml`).
- [ ] Surface the ffprobe / mediainfo preflight inside the GUI (Welcome screen or first-run dialog), mirroring the CLI wizard's check.
- [ ] Document the AppImage in README + docs site once the first tagged release ships.

### 1.4 Homebrew tap (Linux + future macOS)

Set up a tap repo (`r15hav/homebrew-musicorg`) with a single formula. Handles `ffmpeg` + `mediainfo` automatically as Homebrew deps; cross-platform if/when macOS support is added.

**Install path:**
```bash
brew tap R15hav/musicorg
brew install musicorg
```

**Trade-offs.** Adds a separate repo to maintain. Formula is ~30 lines and updates on each release via the same CI tag-push trigger.

**Action items:**
- [ ] Create `r15hav/homebrew-musicorg` tap repo with `Formula/musicorg.rb`.
- [ ] CI workflow that bumps the formula's `url` + `sha256` on tag push (use `brew bump-formula-pr` or write a tiny script).

## Tier 2 — after v1.0 stabilises (broader reach, more maintenance)

### 2.1 AUR (Arch Linux)

`musicorg-bin` + `musicorg-git` PKGBUILDs. Arch users self-serve from `yay` / `paru`. Maintenance is low — PKGBUILDs are ~20 lines and the AUR has community helpers.

The target audience (Linux desktop users comfortable enough to organize their music library from a terminal) has a high Arch/Manjaro share — this is probably the highest-yield Tier-2 channel.

**Action items:**
- [ ] Write `PKGBUILD` + `.SRCINFO`, push to AUR under `musicorg`.

### 2.2 Flatpak via Flathub

Sandboxed, declarative manifest. Gets the app into GNOME Software and KDE Discover with proper search/discovery. Single-package distribution that works on any modern Linux desktop.

**Trade-offs.**
- Sandbox needs to allow read+write access to the user's music directory (`--filesystem=xdg-music` and possibly `--filesystem=host-music`).
- `ffmpeg`/`mediainfo` and the Python runtime ship inside the bundle — no host dependency, but the bundle is ~200 MB.
- Submission process to Flathub takes review cycles (multiple weeks first time).
- gamdl integration is awkward inside Flatpak's sandbox — that extension would probably need to run as a host helper or be disabled in the Flatpak.

**Action items (post-v1.0):**
- [ ] Write `org.musicorg.Musicorg.yaml` Flatpak manifest.
- [ ] Build locally with `flatpak-builder`, test against `tests/fixtures/library-small/`.
- [ ] Submit to Flathub via PR to `flathub/flathub`.

### 2.3 `uvx` / one-shot run

Already works via PyPI publishing — listed here only because it's worth documenting in the README:

```bash
uvx --from 'musicorg[cli]' musicorg     # run without installing
```

Useful for users who want to try the wizard once without committing to an install.

### 2.4 Linux Mint / Pop!_OS PPA

If significant Ubuntu-derived audience emerges. Maintenance is non-trivial (rebuild per Ubuntu release). Probably skip unless there's specific demand — Flatpak covers this audience too.

## Skip list (and why)

| Channel | Why skip |
|---|---|
| **Snap** | Strict confinement breaks `~/Music` access; classic confinement requires a manual Canonical review per release. Flatpak does the same job with less friction. |
| **Docker / OCI** | Wrong shape: musicorg mutates the user's filesystem in-place. Bind-mounting `~/Music` into a container "works" but every undo/snapshot path lands in an inconvenient namespace. |
| **PyInstaller / Nuitka single-binary for the CLI** | Still needs `ffmpeg` + `mediainfo` on the host, so it doesn't solve the "no Python installed" problem for the CLI. Adds 60–200 MB to download for marginal benefit over `pipx install`. (Note: the *GUI* does ship via PyInstaller → AppImage — see §1.3 — because Qt6 / PySide6 makes `pip install` a worse fit for that audience. The CLI keeps the pipx-style path.) |
| **Nuitka for the GUI** | AppImage via PyInstaller already covers the desktop distribution need. Nuitka would be a second packaging system to maintain for no clear gain. |
| **Native `.deb` / `.rpm` in distro repos** | Years of waiting + per-distro maintenance. Flatpak + AUR cover the same audience faster. |
| **Conda / mamba** | Audience is data scientists, not music-library users. Wrong fit. |
| **Windows / macOS native installers** | Deferred, not skipped. Locked phasing: Linux AppImage ships first, then Windows `.exe`, then macOS `.app`. Homebrew (macOS) and Scoop/winget (Windows) become candidates only once those native bundles exist. |

## Release process

Once Tier 1 is live, a single tag triggers everything:

```
$ git tag v0.3.0 && git push --tags
   │
   ├── .github/workflows/publish.yml
   │     • build wheel + sdist
   │     • upload to PyPI via Trusted Publishing (library only)
   │
   ├── .github/workflows/release-appimage.yml
   │     • install GUI build deps + Qt xcb libs
   │     • PyInstaller → AppDir → appimagetool
   │     • attach musicorg-gui-vX.Y.Z-x86_64.AppImage + SHA256SUMS
   │       to the GitHub Release for the tag
   │
   └── homebrew-musicorg/.github/workflows/bump.yml (planned)
         • brew bump-formula-pr opens a PR for the CLI formula
```

## Versioning

- Pre-v1.0: minor bumps for new features, patch for fixes. Breaking changes allowed in minor (we're explicit about this in `PUBLIC_API.md`).
- Post-v1.0: strict SemVer (already documented in `PUBLIC_API.md`).
- Tag format: `vX.Y.Z`. CI strips the `v` prefix when uploading to PyPI.

## Concrete next steps (priority order)

Done:
- [x] **PyPI publishing workflow** — `.github/workflows/publish.yml` (Trusted Publishing on tag push, with TestPyPI via `workflow_dispatch`).
- [x] **`install.sh` for Linux** — detects distro, installs system deps, sets up venv, optionally launches the wizard. Remote one-liner refactor is still pending (see below).
- [x] **GUI AppImage local build pipeline** — `scripts/build_appimage.sh`, `packaging/musicorg-gui.spec`, `packaging/launcher.py`, `packaging/AppDir-template/`. Verified locally producing a 63 MB AppImage that launches.
- [x] **GUI AppImage release workflow** — `.github/workflows/release-appimage.yml` attaches the AppImage + `SHA256SUMS` to the GitHub Release on tag push.
- [x] **Docs site workflow** — `.github/workflows/docs.yml` builds + deploys MkDocs to GitHub Pages.

Outstanding:
1. **Reserve the PyPI name `musicorg`.** (Or pick alternative.) Still the single highest-value action — without this the publish workflow has nothing to publish to.
2. **Refactor `install.sh` for remote `curl | bash`** — clone into `~/.local/share/musicorg-src/` when not run from inside the repo, accept a `--version` flag.
3. **Preflight check** for `ffprobe`/`mediainfo` in both the CLI wizard *and* the GUI Welcome screen, with distro-specific install hints.
4. **First release: `v0.2.1`** to verify both pipelines end-to-end (PyPI wheel + GitHub Release AppImage from a single tag push) before tagging `v0.2.0` proper.
5. **Homebrew tap repo** — second wave after PyPI is confirmed working.
6. **Windows `.exe` GUI build** — second GUI platform per the locked phasing. Reuses the same PyInstaller spec; adds a parallel job/workflow on `windows-latest`.
7. **macOS `.app` GUI build** — third GUI platform. `runs-on: macos-latest`, plus codesigning/notarisation friction.
8. **AUR + Flatpak** — Tier 2, after public API has stabilised (v1.0).
