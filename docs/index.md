# musicorg

A Linux terminal tool that organises a messy music library end-to-end: deduplication, metadata cleanup via iTunes / JioSaavn / Shazam, and optional lossless ALAC upgrade via gamdl. Every phase produces an undo script.

---

## Install and run in three lines

```bash
git clone https://github.com/R15hav/musicorg
cd musicorg
./install.sh
```

`install.sh` detects your distro, installs system tools (`ffmpeg`, `mediainfo`, `xdg-utils`), creates a virtual environment, installs `musicorg[cli]`, and launches the wizard immediately.

Pass `--no-run` if you want to install without launching:

```bash
./install.sh --no-run
```

---

## What the organizer does to your library

Before and after a typical run (Stage 1 only, `move` mode):

```
Before                                   After

~/Music/                                 ~/Music/Music/
├── RISHAV/                              ├── Bollywood/
│   └── 05-Hamdard.mp3  (duplicate)     │   └── 2010s/
├── Ek Villain (2014)/                   │       └── Ek Villain (2014)/
│   ├── 01-Galliyan[Songs.PK].mp3       │           ├── 01 - Galliyan.mp3
│   ├── 02 - Hamdard.mp3                │           ├── 02 - Hamdard.mp3
│   └── 03-Banjaara(PagalWorld).mp3     │           └── 03 - Banjaara.mp3
├── some_cover.jpg                       ├── _duplicates/
└── random.zip                          │   └── RISHAV/05-Hamdard.mp3
                                         └── _misc/
                                             ├── some_cover.jpg
                                             └── random.zip
```

Duplicate losers are quarantined under `_duplicates/`, never deleted. Non-audio files are swept to `_misc/`.

---

## Three stages

| Stage | What it does | Opt-out? |
|---|---|---|
| 1 — File tree | Scan, dedupe, resolve, plan, apply (move/copy/symlink) | Yes |
| 2 — Canonical metadata | iTunes / JioSaavn / Shazam lookup, tag write + rename | Yes |
| 3 — ALAC upgrade | Shazam refingerprint, gamdl download, ffprobe verify | Yes (default off) |

---

## Quick links

- [Install options](install.md) — `install.sh`, `pipx`, `pip`, `uvx`
- [Wizard walkthrough](wizard.md) — the recommended first-run experience
- [CLI reference](cli-reference.md) — all ~20 subcommands
- [ALAC upgrade deep dive](upgrade.md) — gamdl, cookies.txt, permanent-skip taxonomy
- [Architecture](architecture.md) — library/CLI split, state dir layout, undo machinery
- [Troubleshooting](troubleshooting.md) — circuit breaker, year guardrail, PATH issues

---

## For library consumers

If you want to embed musicorg in your own app rather than run the CLI:

```bash
pip install musicorg          # library only; no Typer/Textual deps
```

See the [README](https://github.com/R15hav/musicorg/blob/main/README.md) and [PUBLIC_API.md](https://github.com/R15hav/musicorg/blob/main/PUBLIC_API.md) for the Python API and stability contract.
