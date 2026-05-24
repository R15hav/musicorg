"""MetadataScreen — Stage 2 canonicalization with inline-button review queue.

Separate function from Stage 1 per [[feedback-auto-pipeline]]. Auto-runs
the tiered lookup, then pauses ONLY if there are rows the lookup chain
couldn't auto-apply. Per-row inline buttons (Approve / Skip) replace
the CSV-editor flow from the CLI.

Three states in one screen:

- **Lookup**: CanonicalizeWorker runs; status panel streams per-track
  lookup messages ("'Galliyan' → iTunes (0.95)").
- **Review**: bucket counts at the top; one card per ``review``/``low``
  row with Current vs Match side-by-side and Approve / Skip buttons.
  ``auto_apply`` rows are pre-approved with their api_* match;
  ``no_match`` rows are pre-skipped. Save Approvals → applies.
- **Apply**: ApplyApprovalsWorker writes tags + renames + undo script.

Done state shows the summary and a Back button. Errors surface inline
on the affected card (file gone, snapshot failure, etc.).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from musicorg import ApplyResult, Config, ProgressEvent

from ..widgets import Banner, Pill, StatTile, StatusPanel
from ..workers import ApplyApprovalsWorker, CanonicalizeWorker, LibraryWorker


# Map decision -> Pill state per the design brief.
_DECISION_PILL: dict[str, tuple[str, str]] = {
    "auto_apply": ("done", "auto apply"),
    "review": ("warn", "review"),
    "low": ("warn", "low"),
    "no_match": ("not", "no match"),
}


class _ReviewCard(QFrame):
    """One row of the review queue with inline Approve / Skip buttons."""

    decision_changed = Signal()  # emitted when the user changes pick

    def __init__(self, row: dict, parent: Any = None) -> None:
        super().__init__(parent)
        self._row = row
        self._pick: str | None = self._default_pick()

        self.setProperty("surface", "paper")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(8)

        # Header — decision pill + path
        header = QHBoxLayout()
        header.setSpacing(10)

        decision = (row.get("decision") or "").lower()
        state, label = _DECISION_PILL.get(decision, ("not", decision.replace("_", " ") or "—"))
        self._decision_pill = Pill(label, state)
        header.addWidget(self._decision_pill)

        try:
            confidence = float(row.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence:
            conf_label = QLabel(f"{confidence:.2f}")
            conf_label.setProperty("class", "caption")
            header.addWidget(conf_label)

        path_label = QLabel(Path(row.get("source_path", "")).name)
        path_label.setProperty("class", "caption")
        path_label.setToolTip(row.get("source_path", ""))
        header.addWidget(path_label)
        header.addStretch(1)
        outer.addLayout(header)

        # Current vs Match — two-column row
        two = QHBoxLayout()
        two.setSpacing(24)
        two.addLayout(self._side_block("Current", row, "cur_"), 1)
        two.addLayout(self._side_block("Match", row, "api_"), 1)
        outer.addLayout(two)

        # Action row
        actions = QHBoxLayout()
        actions.addStretch(1)
        self._approve_btn = QPushButton("Approve")
        self._skip_btn = QPushButton("Skip")
        for btn, value in ((self._approve_btn, "itunes"), (self._skip_btn, "skip")):
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, v=value: self._set_pick(v))
            actions.addWidget(btn)
        outer.addLayout(actions)
        self._apply_pick_to_buttons()

    def _default_pick(self) -> str:
        decision = (self._row.get("decision") or "").lower()
        if decision == "auto_apply":
            return "itunes"  # api_* fields carry the chain winner's data
        if decision == "no_match":
            return "skip"
        return ""  # review / low — undecided until the user clicks

    def _side_block(self, title: str, row: dict, prefix: str) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(4)

        head = QLabel(title.upper())
        head.setProperty("class", "footnote")
        layout.addWidget(head)

        title_val = (row.get(f"{prefix}title") or "").strip() or "—"
        artist_val = (row.get(f"{prefix}artist") or "").strip() or "—"
        album_val = (row.get(f"{prefix}album") or "").strip() or "—"
        year_val = (row.get(f"{prefix}year") or "").strip()
        album_year = f"{album_val}{f' ({year_val})' if year_val else ''}"

        # Field labels as caption; values as body.
        for field_label, text in (
            ("Title", title_val),
            ("Artist", artist_val),
            ("Album", album_year),
        ):
            cap = QLabel(field_label)
            cap.setProperty("class", "caption")
            layout.addWidget(cap)
            value = QLabel(text)
            value.setProperty("class", "body")
            value.setWordWrap(True)
            layout.addWidget(value)
        return layout

    def _set_pick(self, value: str) -> None:
        self._pick = value
        self._apply_pick_to_buttons()
        self.decision_changed.emit()

    def _apply_pick_to_buttons(self) -> None:
        approved = self._pick == "itunes"
        skipped = self._pick == "skip"
        self._approve_btn.setChecked(approved)
        self._skip_btn.setChecked(skipped)

        # Swap the variant property for the Approve button based on state.
        self._approve_btn.setProperty("variant", "primary" if approved else None)
        self._approve_btn.style().unpolish(self._approve_btn)
        self._approve_btn.style().polish(self._approve_btn)

        # Skip button stays default — no inline style.
        self._skip_btn.setProperty("variant", None)
        self._skip_btn.style().unpolish(self._skip_btn)
        self._skip_btn.style().polish(self._skip_btn)

    @property
    def pick(self) -> str:
        """One of '', 'itunes', 'skip'. Empty = undecided."""
        return self._pick or ""

    @property
    def source_path(self) -> str:
        return self._row.get("source_path", "")


class _LookupPane(QWidget):
    """Pane shown while CanonicalizeWorker runs."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title = QLabel("Looking up canonical metadata…")
        title.setProperty("class", "h2")
        layout.addWidget(title)

        sub = QLabel(
            "Tiered lookup runs iTunes → JioSaavn → Shazam per track. This pauses "
            "after if any tracks need your review; otherwise it applies on its own."
        )
        sub.setWordWrap(True)
        sub.setProperty("class", "muted")
        layout.addWidget(sub)

        self.status = StatusPanel()
        layout.addWidget(self.status, 1)


class _ReviewPane(QWidget):
    """Pane shown when one or more rows need user review."""

    save_clicked = Signal()
    cancel_clicked = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._title = QLabel("Review canonical metadata")
        self._title.setProperty("class", "h2")
        layout.addWidget(self._title)

        # StatTile row: Total · Auto-approved · Needs review
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(12)
        self._tile_total = StatTile("0", "Total")
        self._tile_auto = StatTile("0", "Auto-approved")
        self._tile_review = StatTile("0", "Needs review")
        tiles_row.addWidget(self._tile_total, 1)
        tiles_row.addWidget(self._tile_auto, 1)
        tiles_row.addWidget(self._tile_review, 1)
        layout.addLayout(tiles_row)

        # One-line caption explaining the flow (replaces the old bucket dump).
        self._buckets = QLabel(
            "Auto-applied matches and no-match rows are pre-decided; flagged rows "
            "below need your call before we write to disk."
        )
        self._buckets.setWordWrap(True)
        self._buckets.setProperty("class", "muted")
        layout.addWidget(self._buckets)

        # Bulk action row
        bulk = QHBoxLayout()
        bulk.addStretch(1)
        self._approve_all = QPushButton("Approve all matches")
        self._approve_all.clicked.connect(self._on_approve_all)
        bulk.addWidget(self._approve_all)
        self._skip_all = QPushButton("Skip all")
        self._skip_all.clicked.connect(self._on_skip_all)
        bulk.addWidget(self._skip_all)
        layout.addLayout(bulk)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_container = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(10)
        scroll.setWidget(self._cards_container)
        layout.addWidget(scroll, 1)

        self._progress_caption = QLabel("")
        self._progress_caption.setProperty("class", "caption")
        layout.addWidget(self._progress_caption)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._cancel_btn = QPushButton("Back")
        self._cancel_btn.setProperty("variant", "ghost")
        self._cancel_btn.clicked.connect(self.cancel_clicked)
        actions.addWidget(self._cancel_btn)
        self._save_btn = QPushButton("Save & apply →")
        self._save_btn.setProperty("variant", "commit")
        self._save_btn.clicked.connect(self.save_clicked)
        actions.addWidget(self._save_btn)
        layout.addLayout(actions)

        self._cards: list[_ReviewCard] = []

    def populate(self, rows: list[dict], bucket_counts: dict[str, int]) -> None:
        # Clear existing cards
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._cards.clear()

        total = sum(bucket_counts.values())
        auto = bucket_counts.get("auto_apply", 0)
        review = bucket_counts.get("review", 0) + bucket_counts.get("low", 0)
        self._tile_total.set_value(total)
        self._tile_auto.set_value(auto)
        self._tile_review.set_value(review)

        # Show review/low rows for user picking; show auto_apply too so the user
        # can see what'll happen (pre-approved). no_match rows are shown muted.
        priority = {"review": 0, "low": 1, "auto_apply": 2, "no_match": 3}
        ordered = sorted(rows, key=lambda r: priority.get((r.get("decision") or "").lower(), 9))
        for row in ordered:
            card = _ReviewCard(row)
            card.decision_changed.connect(self._update_progress_caption)
            self._cards_layout.addWidget(card)
            self._cards.append(card)
        self._cards_layout.addStretch(1)
        self._update_progress_caption()

    def picks(self) -> list[dict]:
        """Build the approvals list — entries for any non-skip / non-empty pick."""
        out: list[dict] = []
        for card in self._cards:
            pick = card.pick
            if pick in ("", "skip"):
                continue
            out.append({"source_path": card.source_path, "pick": pick})
        return out

    @property
    def has_pending_decisions(self) -> bool:
        """True iff at least one row is still undecided."""
        return any(c.pick == "" for c in self._cards)

    def _on_approve_all(self) -> None:
        for card in self._cards:
            card._set_pick("itunes")

    def _on_skip_all(self) -> None:
        for card in self._cards:
            card._set_pick("skip")

    def _update_progress_caption(self) -> None:
        total = len(self._cards)
        approved = sum(1 for c in self._cards if c.pick == "itunes")
        skipped = sum(1 for c in self._cards if c.pick == "skip")
        pending = total - approved - skipped
        self._progress_caption.setText(
            f"{approved} approved · {skipped} skipped · {pending} undecided"
        )
        self._save_btn.setEnabled(pending == 0 or total == 0)


class _DonePane(QWidget):
    back_clicked = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._title = QLabel("Metadata applied")
        self._title.setProperty("class", "h2")
        layout.addWidget(self._title)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setProperty("class", "body")
        layout.addWidget(self._summary)

        self._undo_caption = QLabel("")
        self._undo_caption.setWordWrap(True)
        self._undo_caption.setProperty("class", "caption")
        layout.addWidget(self._undo_caption)

        layout.addStretch(1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._back_btn = QPushButton("← Back")
        self._back_btn.clicked.connect(self.back_clicked)
        actions.addWidget(self._back_btn)
        layout.addLayout(actions)

    def show_for(self, result: ApplyResult, *, was_dry_run: bool = False) -> None:
        title = "Metadata applied (dry run)" if was_dry_run else "Metadata applied"
        self._title.setText(title)
        bits = [
            f"{result.files_touched} files touched",
            f"{result.errors} errors" if result.errors else "",
            f"{result.guarded} year-mismatch guards" if result.guarded else "",
            f"{result.renamed} renames" if result.renamed else "",
        ]
        self._summary.setText("  ·  ".join(b for b in bits if b))
        if result.undo_script_path:
            self._undo_caption.setText(f"Undo script: {result.undo_script_path}")
        else:
            self._undo_caption.setText("")


class _FailedPane(QWidget):
    back_clicked = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title = QLabel("Metadata lookup failed")
        title.setProperty("class", "h2")
        layout.addWidget(title)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setProperty("class", "body")
        layout.addWidget(self.message)

        layout.addStretch(1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        back_btn = QPushButton("← Back")
        back_btn.clicked.connect(self.back_clicked)
        actions.addWidget(back_btn)
        layout.addLayout(actions)


class MetadataScreen(QWidget):
    """Stage 2 entry point — owns the canonicalize + approve + apply flow."""

    back_requested = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg: Config | None = None
        self._worker: LibraryWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(16)

        self._title = QLabel("Retrieve metadata")
        self._title.setProperty("class", "h2")
        outer.addWidget(self._title)

        self._stack = QStackedWidget()
        self._lookup_pane = _LookupPane()
        self._review_pane = _ReviewPane()
        self._apply_pane = _LookupPane()  # reuse a simple status pane during apply
        self._apply_pane.findChild(QLabel).setText("Applying approvals…")  # type: ignore[union-attr]
        self._done_pane = _DonePane()
        self._failed_pane = _FailedPane()
        for pane in (
            self._lookup_pane,
            self._review_pane,
            self._apply_pane,
            self._done_pane,
            self._failed_pane,
        ):
            self._stack.addWidget(pane)
        outer.addWidget(self._stack, 1)

        self._review_pane.save_clicked.connect(self._on_save_approvals)
        self._review_pane.cancel_clicked.connect(self.back_requested)
        self._done_pane.back_clicked.connect(self.back_requested)
        self._failed_pane.back_clicked.connect(self.back_requested)

    # ---- public API ----
    def show_for(self, cfg: Config) -> None:
        self._cfg = cfg
        self._title.setText(f"Retrieve metadata — {cfg.library_slug}")
        self._lookup_pane.status.reset()
        self._stack.setCurrentWidget(self._lookup_pane)
        self._run_canonicalize()

    # ---- canonicalize step ----
    def _run_canonicalize(self) -> None:
        assert self._cfg is not None
        self._worker = CanonicalizeWorker(self._cfg, parent=self)
        self._worker.progress.connect(self._lookup_pane.status.push)
        self._worker.finished_with_result.connect(self._on_canonicalize_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    @Slot(object)
    def _on_canonicalize_done(self, stats: dict[str, int]) -> None:
        assert self._cfg is not None
        rows = self._read_merged()

        # If nothing to review or apply, skip straight to done with an empty
        # ApplyResult — happens on empty libraries or all-no-match runs.
        actionable = stats.get("auto_apply", 0) + stats.get("review", 0) + stats.get("low", 0)
        if actionable == 0:
            from musicorg import ApplyResult as _AR  # noqa: F401  (clarity)
            from musicorg.models import ApplyResult as AR
            self._done_pane.show_for(AR())
            self._stack.setCurrentWidget(self._done_pane)
            return

        # If review/low buckets are empty, we can fast-path: auto-approve
        # auto_apply rows and skip straight to applying.
        if stats.get("review", 0) == 0 and stats.get("low", 0) == 0:
            picks = [
                {"source_path": r.get("source_path", ""), "pick": "itunes"}
                for r in rows if (r.get("decision") or "").lower() == "auto_apply"
            ]
            self._write_approvals_and_apply(picks)
            return

        self._review_pane.populate(rows, stats)
        self._stack.setCurrentWidget(self._review_pane)

    def _read_merged(self) -> list[dict]:
        assert self._cfg is not None
        merged = Path(self._cfg.state_dir) / "16_merged.csv"
        if not merged.exists():
            return []
        with merged.open(newline="") as fh:
            return list(csv.DictReader(fh))

    # ---- approval + apply step ----
    @Slot()
    def _on_save_approvals(self) -> None:
        picks = self._review_pane.picks()
        if not picks:
            answer = QMessageBox.question(
                self,
                "No approvals to apply",
                "Every row is set to Skip. There are no changes to apply.\n\n"
                "Go back to the review queue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                return
            self.back_requested.emit()
            return
        self._write_approvals_and_apply(picks)

    def _write_approvals_and_apply(self, picks: list[dict]) -> None:
        assert self._cfg is not None
        approvals_path = Path(self._cfg.state_dir) / "19_approvals.json"
        approvals_path.write_text(json.dumps(picks, indent=2), encoding="utf-8")

        self._apply_pane.status.reset()
        self._stack.setCurrentWidget(self._apply_pane)

        self._worker = ApplyApprovalsWorker(self._cfg, approvals_path, parent=self)
        self._worker.progress.connect(self._apply_pane.status.push)
        self._worker.finished_with_result.connect(self._on_apply_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    @Slot(object)
    def _on_apply_done(self, result: ApplyResult) -> None:
        self._done_pane.show_for(result)
        self._stack.setCurrentWidget(self._done_pane)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._failed_pane.message.setText(message)
        self._stack.setCurrentWidget(self._failed_pane)
