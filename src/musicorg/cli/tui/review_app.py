"""TUI for editing ``09_plan.csv`` interactively.

This is the plan-table editor referenced in TERMINAL_APP_PLAN section 8.1.
The app loads the CSV into a Textual DataTable, keeps an in-memory
mutation buffer, and only writes back on explicit save (``s``) or
quit-with-save (``q`` → confirm).

Concurrency note: this is a single-user, single-process editor — there is
no file locking. If two ``musicorg review --interactive`` invocations
race on the same plan CSV, last-writer-wins. The pipeline is intended to
be driven sequentially.
"""

from __future__ import annotations

import csv
import subprocess
from pathlib import Path
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static


# Columns surfaced in the TUI. The on-disk CSV may carry additional
# columns; we preserve them on write via the ``_extra_fields`` buffer.
_VISIBLE_COLUMNS: list[str] = [
    "country",
    "album_or_movie",
    "year",
    "title",
    "artist",
    "destination",
]


class _EditCellModal(ModalScreen[str | None]):
    """Single-line input modal used by ``e`` and ``d``.

    Dismisses with the entered string on Enter, or ``None`` on Esc.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    _EditCellModal {
        align: center middle;
    }
    _EditCellModal > Vertical {
        background: $surface;
        border: thick $primary;
        padding: 1 2;
        width: 70;
        height: auto;
    }
    """

    def __init__(self, title: str, initial: str) -> None:
        super().__init__()
        self._title = title
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, id="edit-title")
            yield Input(value=self._initial, id="edit-input")

    def on_mount(self) -> None:
        self.query_one("#edit-input", Input).focus()

    @on(Input.Submitted, "#edit-input")
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class _FilterModal(ModalScreen[str | None]):
    """Substring filter prompt. Empty string clears the filter."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    _FilterModal {
        align: center middle;
    }
    _FilterModal > Vertical {
        background: $surface;
        border: thick $primary;
        padding: 1 2;
        width: 60;
        height: auto;
    }
    """

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Filter (empty to clear):", id="filter-title")
            yield Input(value=self._initial, id="filter-input")

    def on_mount(self) -> None:
        self.query_one("#filter-input", Input).focus()

    @on(Input.Submitted, "#filter-input")
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class _QuitConfirmModal(ModalScreen[str]):
    """Three-way quit prompt: save, discard, or cancel."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    _QuitConfirmModal {
        align: center middle;
    }
    _QuitConfirmModal > Vertical {
        background: $surface;
        border: thick $primary;
        padding: 1 2;
        width: 60;
        height: auto;
    }
    Button {
        margin: 1 1 0 0;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Unsaved changes — save before quitting?")
            yield Button("Save & quit", id="save", variant="primary")
            yield Button("Discard", id="discard", variant="error")
            yield Button("Cancel", id="cancel")

    @on(Button.Pressed)
    def _press(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "cancel")

    def action_cancel(self) -> None:
        self.dismiss("cancel")


class PlanReviewApp(App[int]):
    """Interactive editor for ``09_plan.csv``.

    Constructor takes the path to the plan CSV. Exit code conventions::

        0  -> saved + quit (or save then exit)
        1  -> quit without save

    The mutation buffer stores per-row dicts keyed by row index; the
    DataTable is a *view* over this buffer. Saves rewrite the CSV in
    place, preserving any columns the TUI doesn't surface.
    """

    BINDINGS = [
        Binding("j", "next_row", "Next", show=True),
        Binding("down", "next_row", "Next", show=False),
        Binding("k", "prev_row", "Prev", show=True),
        Binding("up", "prev_row", "Prev", show=False),
        Binding("g", "goto_top", "Top", show=True),
        Binding("G", "goto_bottom", "Bottom", show=True),
        Binding("slash", "filter", "Filter", show=True),
        Binding("e", "edit_cell", "Edit", show=True),
        Binding("d", "reroute", "Reroute", show=True),
        Binding("o", "open_folder", "Open dir", show=True),
        Binding("space", "tag_preview", "Preview", show=True),
        Binding("s", "save", "Save", show=True),
        Binding("q", "quit_app", "Quit", show=True),
    ]

    CSS = """
    Screen {
        layout: vertical;
    }
    #plan-table {
        height: 1fr;
    }
    #status-bar {
        height: auto;
        min-height: 1;
        max-height: 7;
        background: $boost;
        padding: 0 1;
    }
    """

    def __init__(self, plan_csv_path: Path) -> None:
        super().__init__()
        self.plan_csv_path: Path = Path(plan_csv_path)
        # Mutation buffer: parallel list to on-disk row order.
        self._rows: list[dict[str, str]] = []
        # All-field fieldnames from the on-disk CSV (preserve unknown cols).
        self._fieldnames: list[str] = []
        # Row indices currently displayed (after filter).
        self._visible: list[int] = []
        self._filter: str = ""
        self._dirty: bool = False
        self._save_intent: bool = False
        self._exit_code: int = 1

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield DataTable(id="plan-table", cursor_type="cell", zebra_stripes=True)
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._load_csv()
        table = self.query_one("#plan-table", DataTable)
        for col in _VISIBLE_COLUMNS:
            table.add_column(col, key=col)
        self._refresh_table()
        self._set_status(f"Loaded {len(self._rows)} rows from {self.plan_csv_path}")

    # ------------------------------------------------------------------ #
    # CSV I/O
    # ------------------------------------------------------------------ #

    def _load_csv(self) -> None:
        if not self.plan_csv_path.exists():
            self._set_status(f"ERROR: {self.plan_csv_path} not found")
            self._rows = []
            self._fieldnames = list(_VISIBLE_COLUMNS)
            self._visible = []
            return
        with self.plan_csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            self._fieldnames = list(reader.fieldnames or [])
            self._rows = [dict(r) for r in reader]
        # Ensure visible columns exist in the fieldname set so saves can
        # round-trip them. Missing ones get appended at the end.
        for c in _VISIBLE_COLUMNS:
            if c not in self._fieldnames:
                self._fieldnames.append(c)
        self._visible = list(range(len(self._rows)))

    def _save_csv(self) -> bool:
        try:
            with self.plan_csv_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self._fieldnames)
                writer.writeheader()
                for r in self._rows:
                    # DictWriter would raise on missing keys; coerce all
                    # cells to strings so int/None can't sneak in.
                    out = {k: str(r.get(k, "") or "") for k in self._fieldnames}
                    writer.writerow(out)
        except Exception as exc:  # pragma: no cover - filesystem errors
            self._set_status(f"Save failed: {exc}")
            return False
        self._dirty = False
        self._set_status(f"Saved {len(self._rows)} rows -> {self.plan_csv_path}")
        return True

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def _refresh_table(self) -> None:
        table = self.query_one("#plan-table", DataTable)
        table.clear()
        self._visible = self._compute_visible()
        for idx in self._visible:
            row = self._rows[idx]
            table.add_row(
                *[str(row.get(c, "") or "") for c in _VISIBLE_COLUMNS],
                key=str(idx),
            )
        if self._visible:
            table.move_cursor(row=0, column=0)

    def _compute_visible(self) -> list[int]:
        if not self._filter:
            return list(range(len(self._rows)))
        needle = self._filter.lower()
        out: list[int] = []
        for i, r in enumerate(self._rows):
            for c in _VISIBLE_COLUMNS:
                if needle in str(r.get(c, "") or "").lower():
                    out.append(i)
                    break
        return out

    def _set_status(self, msg: str) -> None:
        bar = self.query_one("#status-bar", Static)
        bar.update(msg)

    # ------------------------------------------------------------------ #
    # Cursor helpers
    # ------------------------------------------------------------------ #

    def _current_row_index(self) -> int | None:
        """Return the absolute row index for the highlighted cell."""
        table = self.query_one("#plan-table", DataTable)
        if not self._visible:
            return None
        try:
            cursor_row = table.cursor_row
        except Exception:
            return None
        if cursor_row is None or cursor_row < 0 or cursor_row >= len(self._visible):
            return None
        return self._visible[cursor_row]

    def _current_column_name(self) -> str | None:
        table = self.query_one("#plan-table", DataTable)
        try:
            col = table.cursor_column
        except Exception:
            return None
        if col is None or col < 0 or col >= len(_VISIBLE_COLUMNS):
            return None
        return _VISIBLE_COLUMNS[col]

    # ------------------------------------------------------------------ #
    # Navigation actions
    # ------------------------------------------------------------------ #

    def action_next_row(self) -> None:
        table = self.query_one("#plan-table", DataTable)
        table.action_cursor_down()

    def action_prev_row(self) -> None:
        table = self.query_one("#plan-table", DataTable)
        table.action_cursor_up()

    def action_goto_top(self) -> None:
        table = self.query_one("#plan-table", DataTable)
        if self._visible:
            table.move_cursor(row=0, column=table.cursor_column or 0)

    def action_goto_bottom(self) -> None:
        table = self.query_one("#plan-table", DataTable)
        if self._visible:
            table.move_cursor(row=len(self._visible) - 1, column=table.cursor_column or 0)

    # ------------------------------------------------------------------ #
    # Filter / edit
    # ------------------------------------------------------------------ #

    def action_filter(self) -> None:
        def _apply(value: str | None) -> None:
            if value is None:
                return
            self._filter = value.strip()
            self._refresh_table()
            self._set_status(
                f"Filter: {self._filter!r} -> {len(self._visible)} rows"
                if self._filter else "Filter cleared"
            )
        self.push_screen(_FilterModal(self._filter), _apply)

    def action_edit_cell(self) -> None:
        row_idx = self._current_row_index()
        col_name = self._current_column_name()
        if row_idx is None or col_name is None:
            self._set_status("No cell under cursor")
            return
        self._edit_field(row_idx, col_name, title=f"Edit {col_name}")

    def action_reroute(self) -> None:
        row_idx = self._current_row_index()
        if row_idx is None:
            self._set_status("No row under cursor")
            return
        self._edit_field(row_idx, "destination", title="Reroute destination")

    def _edit_field(self, row_idx: int, col_name: str, title: str) -> None:
        current = str(self._rows[row_idx].get(col_name, "") or "")

        def _apply(value: str | None) -> None:
            if value is None:
                return
            if value == current:
                return
            self._rows[row_idx][col_name] = value
            self._dirty = True
            self._update_row_view(row_idx)
            self._set_status(f"Edited row {row_idx} {col_name} -> {value!r}")

        self.push_screen(_EditCellModal(title, current), _apply)

    def _update_row_view(self, row_idx: int) -> None:
        """Mirror buffer changes into the visible DataTable cell."""
        table = self.query_one("#plan-table", DataTable)
        if row_idx not in self._visible:
            return
        try:
            visible_pos = self._visible.index(row_idx)
        except ValueError:
            return
        row = self._rows[row_idx]
        for col_pos, col in enumerate(_VISIBLE_COLUMNS):
            table.update_cell_at(
                Coordinate(visible_pos, col_pos),
                str(row.get(col, "") or ""),
            )

    # ------------------------------------------------------------------ #
    # External actions (file mgr, tag preview)
    # ------------------------------------------------------------------ #

    def action_open_folder(self) -> None:
        row_idx = self._current_row_index()
        if row_idx is None:
            self._set_status("No row under cursor")
            return
        row = self._rows[row_idx]
        candidate = (
            row.get("source_path")
            or row.get("source_folder")
            or row.get("current_folder")
            or row.get("path")
            or ""
        )
        folder = Path(candidate)
        # If we got a file path, open its parent folder.
        if folder.is_file():
            folder = folder.parent
        if not folder.exists():
            self._set_status(f"Folder not found: {folder}")
            return
        try:
            subprocess.Popen(  # noqa: S603 - controlled args
                ["xdg-open", str(folder)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._set_status(f"xdg-open {folder}")
        except FileNotFoundError:
            self._set_status("xdg-open not installed on this system")
        except Exception as exc:  # pragma: no cover
            self._set_status(f"xdg-open failed: {exc}")

    def action_tag_preview(self) -> None:
        """5-line summary of current row's tag vs proposed values."""
        row_idx = self._current_row_index()
        if row_idx is None:
            self._set_status("No row under cursor")
            return
        row = self._rows[row_idx]

        def _pair(label: str, old_key: str, new_key: str) -> str:
            old = str(row.get(old_key, "") or "")
            new = str(row.get(new_key, "") or "")
            return f"{label:<8} {old!r}  ->  {new!r}"

        # Falls back to plan-only fields when *_old / *_new aren't present.
        lines = [
            _pair("title", "cur_title", "title"),
            _pair("artist", "cur_artist", "artist"),
            _pair("album", "cur_album", "album_or_movie"),
            _pair("year", "cur_year", "year"),
            _pair("dest", "current_folder", "destination"),
        ]
        self._set_status("\n".join(lines))

    # ------------------------------------------------------------------ #
    # Save / quit
    # ------------------------------------------------------------------ #

    def action_save(self) -> None:
        ok = self._save_csv()
        if ok:
            self._save_intent = True

    def action_quit_app(self) -> None:
        if not self._dirty:
            self._exit_code = 0 if self._save_intent else 1
            self.exit(self._exit_code)
            return

        def _on_choice(choice: str) -> None:
            if choice == "save":
                if self._save_csv():
                    self._exit_code = 0
                    self.exit(0)
            elif choice == "discard":
                self._exit_code = 1
                self.exit(1)
            # cancel -> stay open

        self.push_screen(_QuitConfirmModal(), _on_choice)
