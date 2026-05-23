#!/usr/bin/env bash
# musicorg installer (Linux)
#
# Installs:
#   - System tools: ffmpeg, mediainfo, xdg-utils (via apt/dnf/pacman/zypper)
#   - Python venv at .venv/ (or --user fallback) with musicorg + Typer + Textual + mutagen
#   - Optional: shazamio (Shazam audio fingerprinting), gamdl (Apple Music ALAC downloader)
#
# Usage:
#   ./install.sh                  # interactive: asks before installing optional pieces
#   ./install.sh --full           # install everything non-interactively
#   ./install.sh --base           # base only (no Shazam, no gamdl)
#   ./install.sh --user           # install into ~/.local instead of a venv
#   ./install.sh --uninstall      # remove the app

set -euo pipefail

# ─── colors ──────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    C_BOLD=$'\033[1m'; C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
    C_BOLD=''; C_CYAN=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_DIM=''; C_OFF=''
fi

say()  { printf '%s%s%s\n' "$C_CYAN" "$1" "$C_OFF"; }
ok()   { printf '%s✓%s %s\n' "$C_GREEN" "$C_OFF" "$1"; }
warn() { printf '%s!%s %s\n' "$C_YELLOW" "$C_OFF" "$1"; }
die()  { printf '%s✗%s %s\n' "$C_RED" "$C_OFF" "$1" >&2; exit 1; }

# ─── flags ───────────────────────────────────────────────────────────────────
MODE=interactive       # interactive | full | base
USE_USER=0
DO_UNINSTALL=0
SKIP_SYSTEM=0
for arg in "$@"; do
    case "$arg" in
        --full)      MODE=full ;;
        --base)      MODE=base ;;
        --user)      USE_USER=1 ;;
        --uninstall) DO_UNINSTALL=1 ;;
        --skip-system) SKIP_SYSTEM=1 ;;
        -h|--help)
            sed -n '2,15p' "$0"; exit 0 ;;
        *) die "unknown flag: $arg" ;;
    esac
done

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$REPO_DIR/.venv"

# ─── uninstall path ──────────────────────────────────────────────────────────
if [ "$DO_UNINSTALL" -eq 1 ]; then
    say "uninstalling musicorg"
    if [ -d "$VENV_DIR" ]; then
        rm -rf "$VENV_DIR"; ok "removed $VENV_DIR"
    fi
    if pip3 show --user musicorg >/dev/null 2>&1; then
        pip3 uninstall --break-system-packages -y musicorg || true
        ok "removed user-install musicorg"
    fi
    echo
    warn "config + state were left in place:"
    echo "  ~/.config/musicorg/"
    echo "  ~/.local/share/musicorg/"
    echo "delete those manually if you want a clean slate."
    exit 0
fi

# ─── distro detection ────────────────────────────────────────────────────────
detect_pm() {
    if   command -v apt-get >/dev/null 2>&1; then echo "apt"
    elif command -v dnf     >/dev/null 2>&1; then echo "dnf"
    elif command -v pacman  >/dev/null 2>&1; then echo "pacman"
    elif command -v zypper  >/dev/null 2>&1; then echo "zypper"
    elif command -v apk     >/dev/null 2>&1; then echo "apk"
    else echo "unknown"
    fi
}
PM="$(detect_pm)"

prompt_yes() {
    local q="$1" default="${2:-y}"
    if [ "$MODE" = "full" ]; then return 0; fi
    if [ "$MODE" = "base" ]; then return 1; fi
    local hint="[Y/n]"; [ "$default" = "n" ] && hint="[y/N]"
    printf '%s%s%s %s ' "$C_BOLD" "$q" "$C_OFF" "$hint"
    read -r ans
    ans="${ans:-$default}"
    [[ "$ans" =~ ^[Yy] ]]
}

# ─── sanity checks ───────────────────────────────────────────────────────────
say "${C_BOLD}musicorg installer${C_OFF}"
echo "Repo:     $REPO_DIR"
echo "Mode:     $MODE"
echo "Pkg-mgr:  $PM"
echo

command -v python3 >/dev/null 2>&1 || die "python3 is required"
PYV="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
PYMAJ="$(echo "$PYV" | cut -d. -f1)"
PYMIN="$(echo "$PYV" | cut -d. -f2)"
if [ "$PYMAJ" -lt 3 ] || { [ "$PYMAJ" -eq 3 ] && [ "$PYMIN" -lt 10 ]; }; then
    die "python 3.10+ required (found $PYV)"
fi
ok "python $PYV"

# ─── system deps ─────────────────────────────────────────────────────────────
SYS_PKGS_APT="ffmpeg mediainfo xdg-utils python3-venv python3-pip"
SYS_PKGS_DNF="ffmpeg mediainfo xdg-utils python3-pip"
SYS_PKGS_PACMAN="ffmpeg mediainfo xdg-utils python-pip"
SYS_PKGS_ZYPPER="ffmpeg mediainfo xdg-utils python3-pip"
SYS_PKGS_APK="ffmpeg mediainfo xdg-utils py3-pip"

install_system_deps() {
    case "$PM" in
        apt)    sudo apt-get update -qq && sudo apt-get install -y $SYS_PKGS_APT ;;
        dnf)    sudo dnf install -y $SYS_PKGS_DNF ;;
        pacman) sudo pacman -S --needed --noconfirm $SYS_PKGS_PACMAN ;;
        zypper) sudo zypper install -y $SYS_PKGS_ZYPPER ;;
        apk)    sudo apk add --no-cache $SYS_PKGS_APK ;;
        *)      warn "unknown package manager — install ffmpeg+mediainfo+xdg-utils+pip yourself"; return 1 ;;
    esac
}

if [ "$SKIP_SYSTEM" -eq 0 ]; then
    say "system tools (ffmpeg, mediainfo, xdg-utils)"
    if prompt_yes "Install system packages via $PM?" y; then
        if install_system_deps; then
            ok "system deps installed"
        else
            warn "system dep install failed — continuing anyway (musicorg will degrade gracefully)"
        fi
    fi
fi

# ─── python install ──────────────────────────────────────────────────────────
# [cli] is always required — without it the `musicorg` command has no Typer/Textual/Rich.
EXTRAS="cli"
if prompt_yes "Install shazamio (Shazam audio fingerprinting)?" y; then
    EXTRAS="${EXTRAS},shazam"
fi
PIP_TARGET=".[${EXTRAS}]"

if [ "$USE_USER" -eq 1 ]; then
    say "installing musicorg into ~/.local (user-install)"
    PIP="python3 -m pip install --user --break-system-packages"
    $PIP --upgrade pip wheel >/dev/null
    cd "$REPO_DIR"
    $PIP -e "$PIP_TARGET"
    ok "installed (user)"
    BIN_PATH="$HOME/.local/bin"
else
    say "creating venv at $VENV_DIR"
    python3 -m venv "$VENV_DIR" 2>/dev/null || {
        warn "python3 -m venv failed — falling back to --user install"
        USE_USER=1
        python3 -m pip install --user --break-system-packages --upgrade pip wheel >/dev/null
        cd "$REPO_DIR"
        python3 -m pip install --user --break-system-packages -e "$PIP_TARGET"
        BIN_PATH="$HOME/.local/bin"
    }
    if [ "$USE_USER" -eq 0 ]; then
        # shellcheck disable=SC1091
        . "$VENV_DIR/bin/activate"
        python3 -m pip install --upgrade pip wheel >/dev/null
        cd "$REPO_DIR"
        python3 -m pip install -e "$PIP_TARGET"
        deactivate
        ok "installed (venv)"
        BIN_PATH="$VENV_DIR/bin"
    fi
fi

# ─── optional gamdl ──────────────────────────────────────────────────────────
if prompt_yes "Install gamdl (Apple Music ALAC downloader for the upgrade step)?" n; then
    if [ "$USE_USER" -eq 1 ]; then
        python3 -m pip install --user --break-system-packages gamdl || warn "gamdl install failed"
    else
        # shellcheck disable=SC1091
        . "$VENV_DIR/bin/activate"
        python3 -m pip install gamdl || warn "gamdl install failed"
        deactivate
    fi
    ok "gamdl installed"
    echo "${C_DIM}  Note: gamdl needs an Apple Music subscription, browser cookies (cookies.txt),${C_OFF}"
    echo "${C_DIM}  and a Widevine device file (.wvd). Provide them when prompted during${C_OFF}"
    echo "${C_DIM}  the upgrade stage of 'musicorg organize'.${C_OFF}"
fi

# ─── PATH check ──────────────────────────────────────────────────────────────
say "verifying install"
if [ "$USE_USER" -eq 0 ]; then
    if "$BIN_PATH/musicorg" --help >/dev/null 2>&1; then
        ok "musicorg works at $BIN_PATH/musicorg"
    else
        die "musicorg failed to launch — check the install output above"
    fi
else
    if command -v musicorg >/dev/null 2>&1; then
        ok "musicorg on PATH"
    else
        warn "musicorg not on PATH yet — add this to your shell rc:"
        echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
    fi
fi

# ─── done ────────────────────────────────────────────────────────────────────
echo
say "${C_BOLD}done.${C_OFF}"
echo
if [ "$USE_USER" -eq 0 ]; then
    echo "To run the guided wizard:"
    echo "  ${C_BOLD}$BIN_PATH/musicorg${C_OFF}"
    echo
    echo "Or activate the venv first:"
    echo "  ${C_BOLD}. $VENV_DIR/bin/activate${C_OFF}"
    echo "  ${C_BOLD}musicorg${C_OFF}"
else
    echo "To run the guided wizard:"
    echo "  ${C_BOLD}musicorg${C_OFF}"
fi
echo
echo "${C_DIM}Tip: the bare 'musicorg' command launches the interactive wizard.${C_OFF}"
echo "${C_DIM}     'musicorg --help' lists all individual commands for power users.${C_OFF}"
