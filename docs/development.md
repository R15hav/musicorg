# Development

---

## Setup

```bash
git clone https://github.com/R15hav/musicorg
cd musicorg
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[cli,dev]'
```

This installs both `musicorg` and `musicorg_cli` in editable mode, plus `pytest` and `pytest-asyncio`.

To also install the MkDocs dependencies for working on the docs site:

```bash
pip install -e '.[cli,dev,docs]'
mkdocs serve
```

---

## Running tests

```bash
pytest
```

The test suite requires Python 3.10+.

---

## Building the fixture library

The integration tests use a small fixture library that covers every code path: albums with `(YYYY)` folder naming, junk-laden filenames, duplicates across folders, a Hollywood artist for the allowlist routing, and non-audio files for `misc-sweep`.

```bash
python3 tests/fixtures/build_fixture.py
ls tests/fixtures/library-small/
```

Run the fixture rebuild whenever you change `scan.py`, `dedupe.py`, `resolve.py`, or `planner.py` to keep the fixture current.

---

## Running the fixture through the pipeline

```bash
musicorg
# At "Music folder to organize": tests/fixtures/library-small
# At "Library name": demo
# At "Default country": bollywood
# At "How should files be placed?": move
# Confirm Stage 1, decline Stages 2 and 3
```

Expected Stage 1 outcome: 40 winners routed, 4 dup losers quarantined, site-junk stripped from filenames.

---

## Project layout for contributors

```
src/musicorg/           library — edit this for pipeline logic changes
src/musicorg_cli/       CLI consumer — edit this for wizard/TUI/command changes
examples/               embedding patterns — update when the API changes
tests/                  pytest suite
tests/fixtures/         fixture library + build script
docs/                   MkDocs source (this site)
PUBLIC_API.md           stability contract — update when exported names change
DISTRIBUTION.md         release channel plan
```

---

## Before opening a PR

- Run `pytest` and ensure all tests pass.
- If you added or renamed a name in `musicorg/__init__.py.__all__`, update `PUBLIC_API.md`.
- If you changed wizard prompts, update `docs/wizard.md`.
- If you added or removed a `@app.command`, update `docs/cli-reference.md`.
- If you changed TUI `BINDINGS`, update `docs/tui.md`.
