"""TUI card-form for resolving unknown tracks in ``08_resolved.csv``.

Spec: TERMINAL_APP_PLAN section 8.2. Only rows whose ``needs_lookup``
column is non-empty appear in the card stack. Manual entries are
written back to the same ``08_resolved.csv`` using ``manual_*`` columns
(added on save if absent), so downstream stages (planner) can pick them
up via the resolved-row schema.

Bulk-apply broadcasts the current card's four fields across every row
in the same parent folder, regardless of which subset of those rows are
visible — the user's intent is "this whole folder is the same release".
"""

from __future__ import annotations

import csv
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Static


# Manual-entry fields written back to the CSV. Names mirror the
# ``manual_*`` columns used elsewhere in the pipeline so the planner
# can promote them without an additional mapping.
_MANUAL_FIELDS: tuple[str, ...] = ("title", "artist", "album", "year")
_MANUAL_COLUMNS: tuple[str, ...] = tuple(f"manual_{f}" for f in _MANUAL_FIELDS)


class _BulkConfirmModal(ModalScreen[bool]):
    """Yes/no confirmation for the ``b`` bulk-apply action."""

    BINDINGS = [
        Binding("y", "yes", "Yes"),
        Binding("n", "no", "No"),
        Binding("escape", "no", "Cancel"),
    ]

    DEFAULT_CSS = """
    _BulkConfirmModal {
        align: center middle;
    }
    _BulkConfirmModal > Vertical {
        background: $surface;
        border: thick $primary;
        padding: 1 2;
        width: 70;
        height: auto;
    }
    """

    def __init__(self, msg: str) -> None:
        super().__init__()
        self._msg = msg

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._msg)
            yield Static("[y] Yes      [n] No")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class FillUnknownsApp(App[int]):
    """Resolve unknown tracks one card at a time."""

    BINDINGS = [
        Binding("j", "next_card", "Next", show=True),
        Binding("k", "prev_card", "Prev", show=True),
        Binding("tab", "cycle_field", "Cycle", show=True),
        Binding("b", "bulk_apply", "Bulk", show=True),
        Binding("s", "save", "Save", show=True),
        Binding("q", "quit_app", "Quit", show=True),
    ]

    CSS = """
    Screen {
        layout: vertical;
    }
    #card {
        height: 1fr;
        padding: 1 2;
    }
    #card-header {
        height: auto;
        text-style: bold;
        background: $boost;
        padding: 0 1;
    }
    .field-row {
        height: 3;
    }
    .field-label {
        width: 20;
        content-align: right middle;
        padding-right: 1;
    }
    .field-input {
        width: 1fr;
    }
    #status-bar {
        height: auto;
        min-height: 1;
        background: $boost;
        padding: 0 1;
    }
    """

    def __init__(self, resolved_csv_path: Path) -> None:
        super().__init__()
        self.resolved_csv_path: Path = Path(resolved_csv_path)
        self._rows: list[dict[str, str]] = []
        self._fieldnames: list[str] = []
        # Indices into ``_rows`` that need a lookup. The cards stack
        # iterates this list.
        self._card_indices: list[int] = []
        self._cursor: int = 0
        self._dirty: bool = False
        self._exit_code: int = 0

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="card"):
            yield Static("", id="card-header")
            yield Static("", id="card-context")
            yield Static("Manual entry:", id="card-section")
            for field in _MANUAL_FIELDS:
                with Horizontal(classes="field-row"):
                    yield Static(f"{field}:", classes="field-label")
                    yield Input(id=f"in-{field}", classes="field-input")
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._load_csv()
        if not self._card_indices:
            self._set_status("No rows with non-empty needs_lookup")
        self._render_card()

    # ------------------------------------------------------------------ #
    # I/O
    # ------------------------------------------------------------------ #

    def _load_csv(self) -> None:
        if not self.resolved_csv_path.exists():
            self._set_status(f"ERROR: {self.resolved_csv_path} not found")
            return
        with self.resolved_csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            self._fieldnames = list(reader.fieldnames or [])
            self._rows = [dict(r) for r in reader]
        for col in _MANUAL_COLUMNS:
            if col not in self._fieldnames:
                self._fieldnames.append(col)
        self._card_indices = [
            i for i, r in enumerate(self._rows)
            if (r.get("needs_lookup") or "").strip()
        ]

    def _save_csv(self) -> bool:
        # Snapshot inputs into the current row before writing.
        self._capture_inputs()
        try:
            with self.resolved_csv_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self._fieldnames)
                writer.writeheader()
                for r in self._rows:
                    out = {k: str(r.get(k, "") or "") for k in self._fieldnames}
                    writer.writerow(out)
        except Exception as exc:  # pragma: no cover - filesystem
            self._set_status(f"Save failed: {exc}")
            return False
        self._dirty = False
        self._set_status(f"Saved -> {self.resolved_csv_path}")
        return True

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def _set_status(self, msg: str) -> None:
        self.query_one("#status-bar", Static).update(msg)

    def _render_card(self) -> None:
        header = self.query_one("#card-header", Static)
        context = self.query_one("#card-context", Static)
        if not self._card_indices:
            header.update("(no cards)")
            context.update("")
            return
        if self._cursor < 0:
            self._cursor = 0
        if self._cursor >= len(self._card_indices):
            self._cursor = len(self._card_indices) - 1
        row = self._rows[self._card_indices[self._cursor]]
        position = f"{self._cursor + 1} of {len(self._card_indices)}"
        parent = row.get("current_folder") or Path(row.get("path", "")).parent.name
        header.update(f"Resolve unknown — {position} — {parent} / {row.get('filename', '')}")
        context_lines = [
            f"Current parent folder : {parent}",
            f"Filename              : {row.get('filename', '')}",
            f"Tag title             : {row.get('title', '')}",
            f"Tag artist            : {row.get('artist', '')}",
            f"needs_lookup          : {row.get('needs_lookup', '')}",
        ]
        context.update("\n".join(context_lines))
        for field in _MANUAL_FIELDS:
            inp = self.query_one(f"#in-{field}", Input)
            inp.value = str(row.get(f"manual_{field}", "") or "")
        # Default focus on the title input.
        self.query_one("#in-title", Input).focus()

    def _capture_inputs(self) -> None:
        """Copy widget values into the current row buffer."""
        if not self._card_indices:
            return
        if self._cursor < 0 or self._cursor >= len(self._card_indices):
            return
        idx = self._card_indices[self._cursor]
        row = self._rows[idx]
        changed = False
        for field in _MANUAL_FIELDS:
            col = f"manual_{field}"
            new_val = self.query_one(f"#in-{field}", Input).value
            if str(row.get(col, "") or "") != new_val:
                row[col] = new_val
                changed = True
        if changed:
            self._dirty = True

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def action_next_card(self) -> None:
        if not self._card_indices:
            return
        self._capture_inputs()
        if self._cursor < len(self._card_indices) - 1:
            self._cursor += 1
            self._render_card()

    def action_prev_card(self) -> None:
        if not self._card_indices:
            return
        self._capture_inputs()
        if self._cursor > 0:
            self._cursor -= 1
            self._render_card()

    def action_cycle_field(self) -> None:
        # Find currently focused field input and advance.
        focused = self.focused
        ids = [f"in-{f}" for f in _MANUAL_FIELDS]
        current_id = getattr(focused, "id", None)
        try:
            i = ids.index(current_id) if current_id in ids else -1
        except ValueError:
            i = -1
        next_id = ids[(i + 1) % len(ids)]
        self.query_one(f"#{next_id}", Input).focus()

    def action_bulk_apply(self) -> None:
        if not self._card_indices:
            return
        self._capture_inputs()
        idx = self._card_indices[self._cursor]
        row = self._rows[idx]
        folder = row.get("current_folder") or Path(row.get("path", "")).parent.name
        if not folder:
            self._set_status("Cannot bulk-apply: row has no parent folder")
            return
        # Count peers (every row in the same folder, regardless of
        # needs_lookup state — the intent is "this whole folder").
        peers = [
            i for i, r in enumerate(self._rows)
            if (r.get("current_folder") or Path(r.get("path", "")).parent.name) == folder
        ]
        n = len(peers)
        msg = f"Apply these fields to all {n} files in folder '{folder}/'? [y/n]"
        manual_values = {f"manual_{f}": row.get(f"manual_{f}", "") for f in _MANUAL_FIELDS}

        def _on_confirm(confirmed: bool) -> None:
            if not confirmed:
                self._set_status("Bulk apply cancelled")
                return
            for i in peers:
                for col, val in manual_values.items():
                    self._rows[i][col] = val
            self._dirty = True
            self._set_status(f"Bulk-applied to {n} rows in {folder!r}")

        self.push_screen(_BulkConfirmModal(msg), _on_confirm)

    def action_save(self) -> None:
        self._save_csv()

    def action_quit_app(self) -> None:
        self._capture_inputs()
        if self._dirty:
            # Auto-save on quit; mirror the spec's "s" semantics so we
            # don't lose unsaved input. If the user wanted to discard
            # they can revert manually — fill is purely additive.
            self._save_csv()
        self.exit(self._exit_code)
