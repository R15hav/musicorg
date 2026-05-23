"""Headline canonical-review TUI.

Implements TERMINAL_APP_PLAN section 8.4: the CSV-edit-reimport flow
that bridges the canonicalize phase and ``canonical-apply``. The screen
stack is:

    LaunchScreen -> (external editor) -> ValidationScreen -> ApplyScreen
                  \\
                   -> BatchRuleScreen -> (preview) -> ApplyScreen

Key contracts honoured here:
    - The export CSV path lives at ``<state_dir>/19_review_<TS>.csv`` and
      is **preserved** after Apply for traceability.
    - The editor process is launched via ``App.suspend()`` so the TUI
      does not render over the editor.
    - Validation surfaces errors with the row number (matching the
      ``row`` column in the CSV) so users can locate offenders quickly.
    - ``apply_approvals`` runs in-process on a worker thread; the
      progress widget is bound to a callback that records ApplyResult
      counters as they're populated.

The in-process apply call is wrapped in ``app.run_worker(thread=True)``
so the TUI event loop stays responsive. The underlying
``canonicalize.apply_approvals`` is synchronous and CPU/IO mixed, so a
thread is the right primitive — not asyncio.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, ProgressBar, Static

from musicorg import approval, canonicalize
from musicorg.models import ApplyResult


_EDITOR_FALLBACKS: tuple[str, ...] = ("vim", "nano")


def _resolve_editor() -> str | None:
    """Pick the editor binary using $VISUAL > $EDITOR > vim > nano."""
    for env_var in ("VISUAL", "EDITOR"):
        candidate = os.environ.get(env_var, "").strip()
        if candidate:
            # Allow flags in $EDITOR (e.g. ``code --wait``); take arg0.
            argv0 = candidate.split()[0]
            if shutil.which(argv0):
                return candidate
    for fallback in _EDITOR_FALLBACKS:
        if shutil.which(fallback):
            return fallback
    return None


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# --------------------------------------------------------------------- #
# Generic modals
# --------------------------------------------------------------------- #


class _MessageModal(ModalScreen[str]):
    """Modal with a free-text body and N buttons. Returns the pressed id."""

    DEFAULT_CSS = """
    _MessageModal {
        align: center middle;
    }
    _MessageModal > Vertical {
        background: $surface;
        border: thick $primary;
        padding: 1 2;
        width: 80;
        height: auto;
    }
    Button {
        margin: 1 1 0 0;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, body: str, choices: list[tuple[str, str]]) -> None:
        """``choices`` is a list of (button_id, label)."""
        super().__init__()
        self._body = body
        self._choices = choices

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._body)
            with Horizontal():
                for bid, label in self._choices:
                    yield Button(label, id=bid)

    @on(Button.Pressed)
    def _press(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "cancel")

    def action_cancel(self) -> None:
        self.dismiss("cancel")


class _RuleInputModal(ModalScreen[str | None]):
    """Free-text input for a tier-priority rule string."""

    DEFAULT_CSS = """
    _RuleInputModal {
        align: center middle;
    }
    _RuleInputModal > Vertical {
        background: $surface;
        border: thick $primary;
        padding: 1 2;
        width: 70;
        height: auto;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Rule (e.g. jiosaavn>shazam>itunes):")
            yield Input(value="jiosaavn>shazam>itunes", id="rule-input")

    def on_mount(self) -> None:
        self.query_one("#rule-input", Input).focus()

    @on(Input.Submitted, "#rule-input")
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------- #
# Screens
# --------------------------------------------------------------------- #


class _LaunchScreen(Screen[None]):
    """Initial screen: shows counts + three action buttons."""

    BINDINGS = [Binding("q", "cancel", "Quit")]

    DEFAULT_CSS = """
    _LaunchScreen {
        align: center middle;
    }
    _LaunchScreen > Vertical {
        background: $surface;
        border: thick $primary;
        padding: 1 2;
        width: 90;
        height: auto;
    }
    Button {
        margin: 1 1 0 0;
    }
    #banner {
        color: $error;
        text-style: bold;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static("", id="title")
            yield Static("", id="counts")
            yield Static("", id="paths")
            yield Static("", id="banner")
            with Horizontal():
                yield Button("Open in editor", id="open-editor", variant="primary")
                yield Button("Use batch rule instead", id="batch-rule")
                yield Button("Cancel", id="cancel")
        yield Footer()

    def on_mount(self) -> None:
        app: CanonicalReviewApp = self.app  # type: ignore[assignment]
        try:
            counts = approval.summarise_buckets(app.merged_csv, app.promote_set)
        except FileNotFoundError:
            counts = {}
        total = sum(counts.values())
        self.query_one("#title", Static).update(
            f"Canonical Review — {total} files need approval after canonicalize"
        )
        self.query_one("#counts", Static).update(
            "\n".join([
                f"  review     : {counts.get('review', 0):>4}",
                f"  low        : {counts.get('low', 0):>4}",
                f"  no_match   : {counts.get('no_match', 0):>4}",
            ])
        )
        self.query_one("#paths", Static).update(
            f"Editor:      {app.editor or '(not found)'}\n"
            f"Export path: {app.export_csv_path}"
        )
        if not app.editor:
            self.query_one("#banner", Static).update(
                "No editor found ($VISUAL, $EDITOR, vim, nano). "
                "Install one or use the batch-rule path."
            )

    @on(Button.Pressed, "#open-editor")
    def _on_open_editor(self) -> None:
        app: CanonicalReviewApp = self.app  # type: ignore[assignment]
        if not app.editor:
            app.notify("No editor found — install vim/nano or set $EDITOR", severity="error")
            return
        app.run_editor_flow()

    @on(Button.Pressed, "#batch-rule")
    def _on_batch_rule(self) -> None:
        app: CanonicalReviewApp = self.app  # type: ignore[assignment]
        app.start_batch_rule_flow()

    @on(Button.Pressed, "#cancel")
    def _on_cancel(self) -> None:
        self.app.exit(1)

    def action_cancel(self) -> None:
        self.app.exit(1)


class _ValidationScreen(Screen[None]):
    """Post-editor screen showing parse_review_csv results."""

    BINDINGS = [Binding("q", "cancel", "Quit")]

    DEFAULT_CSS = """
    _ValidationScreen {
        align: center middle;
    }
    _ValidationScreen > Vertical {
        background: $surface;
        border: thick $primary;
        padding: 1 2;
        width: 100;
        height: auto;
        max-height: 90%;
    }
    Button {
        margin: 1 1 0 0;
    }
    #issues {
        color: $error;
        max-height: 20;
    }
    #ok {
        color: $success;
    }
    """

    def __init__(
        self,
        approval_count: int,
        error_list: list[tuple[int, str, str]] | None,
    ) -> None:
        super().__init__()
        self._approval_count = approval_count
        self._errors = error_list or []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static("Validation", id="title")
            if self._errors:
                yield Static(f"Found {len(self._errors)} issue(s):", id="title2")
                lines = [
                    f"  row {row}  {field}: {issue}"
                    for row, field, issue in self._errors[:200]
                ]
                if len(self._errors) > 200:
                    lines.append(f"  ... and {len(self._errors) - 200} more")
                yield Static("\n".join(lines), id="issues")
                with Horizontal():
                    yield Button("Re-edit CSV", id="re-edit", variant="primary")
                    yield Button("Cancel", id="cancel")
            else:
                yield Static(
                    f"CSV parsed: {self._approval_count} approvals will be applied.",
                    id="ok",
                )
                with Horizontal():
                    yield Button("Apply approvals", id="apply", variant="primary")
                    yield Button("Re-edit CSV", id="re-edit")
                    yield Button("Cancel", id="cancel")
        yield Footer()

    @on(Button.Pressed, "#apply")
    def _on_apply(self) -> None:
        app: CanonicalReviewApp = self.app  # type: ignore[assignment]
        app.start_apply_flow()

    @on(Button.Pressed, "#re-edit")
    def _on_reedit(self) -> None:
        app: CanonicalReviewApp = self.app  # type: ignore[assignment]
        app.run_editor_flow(reuse_existing=True)

    @on(Button.Pressed, "#cancel")
    def _on_cancel(self) -> None:
        self.app.exit(1)

    def action_cancel(self) -> None:
        self.app.exit(1)


class _ApplyScreen(Screen[None]):
    """Progress + summary screen for the in-process apply."""

    BINDINGS = [Binding("q", "exit", "Quit")]

    DEFAULT_CSS = """
    _ApplyScreen {
        align: center middle;
    }
    _ApplyScreen > Vertical {
        background: $surface;
        border: thick $primary;
        padding: 1 2;
        width: 90;
        height: auto;
    }
    Button {
        margin: 1 1 0 0;
    }
    #summary {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static("Applying approvals...", id="title")
            yield ProgressBar(total=100, show_eta=False, id="progress")
            yield Static("", id="summary")
            with Horizontal():
                yield Button("Done", id="done", variant="primary", disabled=True)
        yield Footer()

    @on(Button.Pressed, "#done")
    def _on_done(self) -> None:
        self.app.exit(0)

    def action_exit(self) -> None:
        # Allow quitting even mid-apply; the worker will finish in the
        # background but since exit() unwinds the loop we accept that
        # state is already persisted incrementally by apply_approvals().
        self.app.exit(0)


# --------------------------------------------------------------------- #
# Main App
# --------------------------------------------------------------------- #


class CanonicalReviewApp(App[int]):
    """Canonical-review TUI entrypoint.

    Constructor wiring:

        state_dir       — XDG state dir for the library (Config.state_dir).
        merged_csv      — path to the canonicalize merged CSV
                          (typically ``<state_dir>/16_merged.csv``).
        promote_set     — optional set of source_paths to mark
                          ``auto_apply`` before bucketing for review.

    Internally we derive:
        export_csv_path     ``<state_dir>/19_review_<TS>.csv``
        approvals_json_path ``<state_dir>/19_approvals.json``
        snapshot_path       ``<state_dir>/20_snapshot.json``
        undo_script_path    ``<state_dir>/20_undo.py``
    """

    CSS = """
    Screen {
        background: $surface;
    }
    """

    def __init__(
        self,
        state_dir: Path,
        merged_csv: Path,
        promote_set: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.state_dir: Path = Path(state_dir)
        self.merged_csv: Path = Path(merged_csv)
        self.promote_set: set[str] = set(promote_set or set())
        self.editor: str | None = _resolve_editor()

        ts = _timestamp()
        self.export_csv_path: Path = self.state_dir / f"19_review_{ts}.csv"
        self.approvals_json_path: Path = self.state_dir / "19_approvals.json"
        self.snapshot_path: Path = self.state_dir / "20_snapshot.json"
        self.undo_script_path: Path = self.state_dir / "20_undo.py"

        # Cached batch-rule state across screens.
        self._pending_rule: str | None = None
        # Apply result captured by the worker.
        self._apply_result: ApplyResult | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def on_mount(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.push_screen(_LaunchScreen())

    # ------------------------------------------------------------------ #
    # Editor flow
    # ------------------------------------------------------------------ #

    def run_editor_flow(self, *, reuse_existing: bool = False) -> None:
        """Build/refresh the export CSV, suspend, run editor, validate."""
        if not self.editor:
            self.notify("No editor configured", severity="error")
            return

        # Build the export CSV unless the user explicitly re-edits the
        # same file. The (export_csv_path includes a timestamp so it
        # won't collide with previous runs.)
        if not reuse_existing or not self.export_csv_path.exists():
            try:
                count = approval.build_review_csv(
                    self.merged_csv,
                    self.export_csv_path,
                    self.promote_set,
                )
                self.notify(f"Exported {count} rows to {self.export_csv_path}")
            except FileNotFoundError as exc:
                self.notify(f"Merged CSV missing: {exc}", severity="error")
                return
            except Exception as exc:  # pragma: no cover
                self.notify(f"Export failed: {exc}", severity="error")
                return

        # Suspend the Textual loop, spawn editor, resume on exit.
        editor_argv = self.editor.split() + [str(self.export_csv_path)]
        exit_code = 0
        try:
            with self.suspend():
                completed = subprocess.run(editor_argv, check=False)
                exit_code = completed.returncode
        except FileNotFoundError:
            self.notify(f"Editor binary not found: {self.editor}", severity="error")
            return
        except Exception as exc:  # pragma: no cover
            self.notify(f"Editor invocation failed: {exc}", severity="error")
            return

        if exit_code != 0:
            self._prompt_editor_nonzero(exit_code)
            return

        self._validate_and_push()

    def _prompt_editor_nonzero(self, exit_code: int) -> None:
        def _choice(choice: str) -> None:
            if choice == "re-edit":
                self.run_editor_flow(reuse_existing=True)
            else:
                self.exit(1)

        self.push_screen(
            _MessageModal(
                f"Editor exited with code {exit_code}. Re-edit or cancel?",
                [("re-edit", "Re-edit"), ("cancel", "Cancel")],
            ),
            _choice,
        )

    def _validate_and_push(self) -> None:
        try:
            count, _approvals = approval.parse_review_csv(
                self.export_csv_path,
                self.approvals_json_path,
            )
            self.push_screen(_ValidationScreen(count, None))
        except approval.ApprovalValidationError as exc:
            # Pop validation screen with error list; user can re-edit.
            self.push_screen(_ValidationScreen(0, exc.errors))
        except Exception as exc:  # pragma: no cover
            self.notify(f"Parse failed: {exc}", severity="error")

    # ------------------------------------------------------------------ #
    # Batch-rule flow
    # ------------------------------------------------------------------ #

    def start_batch_rule_flow(self) -> None:
        def _on_rule(value: str | None) -> None:
            if value is None:
                return
            rule = value.strip()
            if not rule:
                self.notify("Empty rule", severity="warning")
                return
            self._preview_batch_rule(rule)

        self.push_screen(_RuleInputModal(), _on_rule)

    def _preview_batch_rule(self, rule: str) -> None:
        """Compute the proposed approvals so the user can confirm before write."""
        try:
            # Run batch_rule against a scratch file to compute the
            # preview without overwriting the real approvals.json yet.
            scratch = self.state_dir / f"19_approvals_preview_{_timestamp()}.json"
            n = approval.batch_rule(self.merged_csv, scratch, rule)
        except ValueError as exc:
            self.notify(f"Invalid rule: {exc}", severity="error")
            return
        except Exception as exc:  # pragma: no cover
            self.notify(f"Batch rule failed: {exc}", severity="error")
            return

        # Compute per-tier counts from the scratch JSON for the preview.
        import json as _json
        try:
            approvals_list = _json.loads(scratch.read_text(encoding="utf-8"))
        except Exception:
            approvals_list = []
        from collections import Counter as _Counter
        by_pick = _Counter(a.get("pick", "") for a in approvals_list)
        preview_msg = (
            f"This will approve "
            f"{by_pick.get('jiosaavn', 0)} jiosaavn / "
            f"{by_pick.get('shazam', 0)} shazam / "
            f"{by_pick.get('itunes', 0)} itunes  "
            f"({n} rows total) — proceed?"
        )

        def _on_confirm(choice: str) -> None:
            try:
                scratch.unlink()
            except FileNotFoundError:
                pass
            if choice != "proceed":
                return
            # Re-run against the real path so the approvals JSON is
            # canonical (avoids depending on the scratch file).
            try:
                approval.batch_rule(self.merged_csv, self.approvals_json_path, rule)
            except Exception as exc:  # pragma: no cover
                self.notify(f"Batch rule failed: {exc}", severity="error")
                return
            self.start_apply_flow()

        self.push_screen(
            _MessageModal(
                preview_msg,
                [("proceed", "Proceed"), ("cancel", "Cancel")],
            ),
            _on_confirm,
        )

    # ------------------------------------------------------------------ #
    # Apply flow
    # ------------------------------------------------------------------ #

    def start_apply_flow(self) -> None:
        self.push_screen(_ApplyScreen())
        self._run_apply_worker()

    @work(thread=True, exclusive=True)
    def _run_apply_worker(self) -> None:  # pragma: no cover - threading
        """Worker that runs apply_approvals and updates the UI."""
        try:
            result = canonicalize.apply_approvals(
                self.merged_csv,
                self.approvals_json_path,
                self.snapshot_path,
                self.undo_script_path,
                dry_run=False,
                rename=True,
            )
        except Exception as exc:
            self.call_from_thread(self._on_apply_error, str(exc))
            return
        self.call_from_thread(self._on_apply_done, result)

    def _on_apply_error(self, msg: str) -> None:
        screen = self.screen
        if isinstance(screen, _ApplyScreen):
            screen.query_one("#summary", Static).update(f"Apply failed: {msg}")
            screen.query_one("#done", Button).disabled = False
            screen.query_one("#progress", ProgressBar).update(progress=0)

    def _on_apply_done(self, result: ApplyResult) -> None:
        self._apply_result = result
        screen = self.screen
        if not isinstance(screen, _ApplyScreen):
            return
        screen.query_one("#progress", ProgressBar).update(total=100, progress=100)
        screen.query_one("#title", Static).update("Apply complete.")
        summary_lines = [
            f"files_touched : {result.files_touched}",
            f"tags_written  : {result.tags_written}",
            f"renamed       : {result.renamed}",
            f"guarded       : {result.guarded}",
            f"collisions    : {result.collisions}",
            f"errors        : {result.errors}",
            f"snapshot      : {result.snapshot_path}",
            f"undo script   : {result.undo_script_path}",
            f"review csv    : {self.export_csv_path}",
        ]
        screen.query_one("#summary", Static).update("\n".join(summary_lines))
        screen.query_one("#done", Button).disabled = False
