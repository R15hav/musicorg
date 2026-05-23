# Install

musicorg is a Linux-only tool. It requires Python 3.10+ and three system tools: `ffmpeg`, `mediainfo`, and `xdg-utils`. Install these via your package manager before or alongside the Python package (the `install.sh` script does this automatically).

---

## Option 1 — install.sh (recommended for end users)

The installer handles system deps, creates a virtualenv, installs the package, and launches the wizard:

```bash
git clone https://github.com/R15hav/musicorg
cd musicorg
./install.sh
```

### Flags

| Flag | Behaviour |
|---|---|
| *(no flags)* | Interactive — asks before each optional piece; launches the wizard at the end |
| `--full` | Install everything non-interactively (system deps + shazamio + gamdl), then launch |
| `--base` | CLI only, no shazamio, no gamdl; then launch |
| `--no-run` | Install only — do not launch the wizard afterwards |
| `--user` | Install into `~/.local` instead of `.venv/` |
| `--skip-system` | Skip the `apt`/`dnf`/`pacman` step (useful if you've already installed system deps) |
| `--uninstall` | Remove the venv or user-install. Leaves config and state in place. |

### What the installer does, step by step

1. Detects your package manager (`apt`, `dnf`, `pacman`, `zypper`, or `apk`).
2. Installs `ffmpeg`, `mediainfo`, `xdg-utils`, and `python3-venv` / `python3-pip`.
3. Creates `.venv/` in the repo directory (falls back to `--user` if `python3-venv` fails).
4. Installs `musicorg[cli]` editable from the local clone.
5. Optionally installs `shazamio` (for Stage 2 Shazam fingerprinting) and `gamdl` (for Stage 3 ALAC upgrade).
6. Verifies the `musicorg` binary works.
7. Launches `musicorg` (the wizard) unless `--no-run` was passed.

### System deps by distro

| Distro family | Command run by install.sh |
|---|---|
| Debian / Ubuntu | `sudo apt-get install ffmpeg mediainfo xdg-utils python3-venv python3-pip` |
| Fedora / RHEL | `sudo dnf install ffmpeg mediainfo xdg-utils python3-pip` |
| Arch / Manjaro | `sudo pacman -S ffmpeg mediainfo xdg-utils python-pip` |
| openSUSE | `sudo zypper install ffmpeg mediainfo xdg-utils python3-pip` |
| Alpine | `sudo apk add ffmpeg mediainfo xdg-utils py3-pip` |

If your package manager is not in this list, install these three tools manually before running `install.sh --skip-system`.

---

## Option 2 — pipx (recommended for CLI-only use without a source clone)

```bash
pipx install 'musicorg[cli]'
```

`pipx` isolates the install in its own virtualenv and puts `musicorg` on your PATH automatically. This is the cleanest option if you already have `pipx` installed.

!!! warning "System deps not handled"
    `pipx` (and all PyPI install paths) do not install `ffmpeg`, `mediainfo`, or `xdg-utils`. Install them with your system package manager using the commands in the table above before running `musicorg`.

---

## Option 3 — pip in a virtualenv

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install 'musicorg[cli]'
musicorg
```

---

## Option 4 — with audio fingerprinting (Shazam)

Add the `shazam` extra to any of the above install methods:

```bash
pip install 'musicorg[cli,shazam]'
# or
pipx install 'musicorg[cli,shazam]'
```

The `shazam` extra installs `shazamio` (aiohttp + pydub + a Rust fingerprinter). It is optional: the wizard will offer to install it at runtime if it is absent when you reach Stage 2.

---

## PATH note for --user installs

If you used `--user` with `install.sh` or ran `pip install --user`, the `musicorg` binary lands at `~/.local/bin/musicorg`. If that directory is not on your `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Add this line to `~/.bashrc` or `~/.zshrc` to make it permanent.

---

## Uninstall

```bash
./install.sh --uninstall
```

This removes the `.venv/` or the user-installed package. Config and library state are left in place. To remove those too:

```bash
rm -rf ~/.config/musicorg ~/.local/share/musicorg
```
