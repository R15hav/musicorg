# TUI Screens

musicorg ships four Textual full-screen interfaces. All require the `[cli]` extra (`pip install musicorg[cli]`).

Source: [`src/musicorg_cli/tui/`](https://github.com/R15hav/musicorg/blob/main/src/musicorg_cli/tui/)

---

## FillUnknownsApp

**Purpose:** Per-row card editor for tracks with missing metadata fields (`needs_lookup` is non-empty after the resolve phase).

**Launch:**

```bash
musicorg --library <name> fill
```

Requires `<state>/08_resolved.csv` to exist (run `resolve` first).

Source: [`src/musicorg_cli/tui/fill_app.py`](https://github.com/R15hav/musicorg/blob/main/src/musicorg_cli/tui/fill_app.py)

### Keybindings

| Key | Action |
|---|---|
| `j` | Next card |
| `k` | Previous card |
| `Tab` | Cycle between editable fields on the current card |
| `b` | Bulk apply the current card's values to all similar rows |
| `s` | Save current edits |
| `q` | Quit |

---

## DupReviewApp

**Purpose:** Two-pane duplicate picker. Shows duplicate groups on the left; file details and audio metadata on the right. Lets you manually override which file the deduper picks as the winner before the resolve and plan phases run.

**Launch:**

```bash
musicorg --library <name> dedupe --interactive
```

Source: [`src/musicorg_cli/tui/dup_review_app.py`](https://github.com/R15hav/musicorg/blob/main/src/musicorg_cli/tui/dup_review_app.py)

### Keybindings

| Key | Action |
|---|---|
| `j` | Next duplicate group |
| `k` | Previous duplicate group |
| `J` | Next member within the current group |
| `K` | Previous member within the current group |
| `w` | Mark the currently highlighted member as the winner |
| `s` | Skip this group (accept the automatic winner) |
| `q` | Save all decisions and quit |

---

## PlanReviewApp

**Purpose:** Row-level viewer and editor for `09_plan.csv`. Useful for inspecting the destination tree before committing an `apply`, and for manually rerouting individual files to different destinations.

**Launch:**

```bash
musicorg --library <name> review --interactive
```

When only `09_plan.csv` is available (i.e., `16_merged.csv` does not yet exist), `review --interactive` opens this screen instead of `CanonicalReviewApp`.

Source: [`src/musicorg_cli/tui/review_app.py`](https://github.com/R15hav/musicorg/blob/main/src/musicorg_cli/tui/review_app.py)

### Keybindings

| Key | Action |
|---|---|
| `j` / `Down` | Next row |
| `k` / `Up` | Previous row |
| `g` | Jump to top |
| `G` | Jump to bottom |
| `/` (slash) | Filter rows |
| `e` | Edit the selected cell inline |
| `d` | Reroute: reassign the selected file's destination |
| `o` | Open the current file's source folder in the file manager |
| `Space` | Preview tags for the selected file |
| `s` | Save changes to `09_plan.csv` |
| `q` | Quit |

---

## CanonicalReviewApp

**Purpose:** Full canonical-review workflow in a single TUI. Loads `16_merged.csv`, lets you apply a batch rule or open the review CSV in your `$EDITOR`, validates the result, and applies tag writes + renames without leaving the terminal.

**Launch:**

```bash
musicorg --library <name> review --interactive
```

This screen is shown when `<state>/16_merged.csv` is present.

Source: [`src/musicorg_cli/tui/canonical_app.py`](https://github.com/R15hav/musicorg/blob/main/src/musicorg_cli/tui/canonical_app.py)

### Screen flow

1. **Launch screen** — shows `auto_apply`, `review`, `low`, and `no_match` counts. Three action buttons: "Apply batch rule", "Edit CSV", "Exit".
2. **Rule input modal** — free-text input for a tier-priority rule (e.g. `jiosaavn>shazam>itunes`). Opened by "Apply batch rule".
3. **Validation screen** — shows the result after the CSV is parsed. Options: "Apply approvals", "Re-edit CSV", "Cancel".
4. **Apply screen** — progress display + final summary after tags are written.

### Keybindings (by screen)

**Launch screen:**

| Key | Action |
|---|---|
| `q` | Quit |

**Rule input modal:**

| Key | Action |
|---|---|
| `Escape` | Cancel / close modal |

**Validation screen:**

| Key | Action |
|---|---|
| `q` | Quit |

**Apply screen:**

| Key | Action |
|---|---|
| `q` | Exit after apply completes |

!!! tip
    All modal dialogs also respond to `Escape` to dismiss them without taking an action.
