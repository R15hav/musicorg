"""PipelineScreen — auto-runs Stage 1 with two approval gates.

Flow:

    scan
      → metadata-lookup (custom-order canonicalize: jiosaavn > itunes > shazam)
        → [user reviews canonical metadata]
        → apply approvals (tag writes + file renames + tag snapshot undo)
      → dedupe
      → resolve  ─┐
      → plan      ┘ (one user-facing "Plan" phase, two workers under the hood)
        → [user approves plan]
      → execute
      → done

A horizontal phase strip at the top shows where the pipeline is via
five pills (``scan · metadata · dedupe · plan · execute``). Resolve
folds into the "plan" pill from the user's perspective. The middle
pane swaps between:

- **Running state**: current phase, translated status line, progress bar.
- **Metadata review**: bucket counts + one card per merged-CSV row with
  inline Approve / Skip. The first approval gate.
- **Plan preview**: summary tiles + per-destination breakdown + Apply /
  Cancel buttons. The second approval gate.
- **Failed state**: error message + Back button.

Emits ``completed(stats)`` when Execute finishes successfully so
MainWindow can transition to CompletionScreen. ``cancelled()`` fires on
user Cancel, ``failed(message)`` on any worker exception.
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
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from musicorg import ApplyResult, Config, ProgressEvent

from ..widgets import Banner, Pill, StatTile, StatusPanel
from ..workers import (
    ApplyApprovalsWorker,
    ApplyMode,
    DedupeOutcome,
    DedupeWorker,
    ExecuteWorker,
    LibraryWorker,
    PlanWorker,
    ResolveWorker,
    ScanWorker,
)
from ..workers.canonicalize_worker import CustomOrderCanonicalizeWorker


_PHASES: list[tuple[str, str]] = [
    ("scan", "Scan"),
    ("metadata", "Metadata"),
    ("dedupe", "Dedupe"),
    ("plan", "Plan"),  # internally: resolve + plan
    ("execute", "Apply"),
]


_STATUS_TO_PILL_STATE: dict[str, str] = {
    "pending": "not",
    "running": "run",
    "done": "done",
    "failed": "block",
}


# Map decision -> Pill state (mirrors metadata.py's _DECISION_PILL).
_DECISION_PILL: dict[str, tuple[str, str]] = {
    "auto_apply": ("done", "auto apply"),
    "review": ("warn", "review"),
    "low": ("warn", "low"),
    "no_match": ("not", "no match"),
}


class _PhaseStrip(QWidget):
    """Horizontal pill row showing per-phase status (pending / running / done).

    Uses the design-language :class:`Pill` widget. Each pill is prefixed
    with a small mono caption indicating phase number so the strip reads
    ``1 Scan · 2 Metadata · 3 Dedupe · 4 Plan · 5 Apply`` with ``→``
    separators between stages.
    """

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._pills: dict[str, Pill] = {}
        self._statuses: dict[str, str] = {}
        for i, (key, label) in enumerate(_PHASES):
            num = QLabel(str(i + 1))
            num.setProperty("class", "mono")
            num.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(num)

            pill = Pill(label, "not")
            self._pills[key] = pill
            self._statuses[key] = "pending"
            layout.addWidget(pill)

            if i < len(_PHASES) - 1:
                sep = QLabel("→")
                sep.setProperty("class", "caption")
                sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(sep)
        layout.addStretch(1)

    def set_status(self, phase: str, status: str) -> None:
        pill = self._pills.get(phase)
        if pill is None:
            return
        self._statuses[phase] = status
        label = dict(_PHASES)[phase]
        pill.set_state(_STATUS_TO_PILL_STATE.get(status, "not"), label)

    def running_phase(self) -> str | None:
        for key, status in self._statuses.items():
            if status == "running":
                return key
        return None


class _RunningPane(QWidget):
    """The middle pane while a worker is active."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        self._layout = layout

        self.headline = QLabel("Preparing…")
        self.headline.setProperty("class", "card-title")
        layout.addWidget(self.headline)

        # Optional reassurance banner shown only during the execute phase.
        self._banner: Banner | None = None

        self.status = StatusPanel()
        layout.addWidget(self.status, 1)

    def set_phase(self, phase: str) -> None:
        """Show / hide the "reversible" banner based on the active phase."""
        if phase == "execute":
            if self._banner is None:
                self._banner = Banner(
                    severity="info",
                    title="This phase is reversible",
                    body="An undo script will be written before any file is moved.",
                )
                # Insert directly above the StatusPanel (index 1, after headline).
                self._layout.insertWidget(1, self._banner)
            self._banner.setVisible(True)
        else:
            if self._banner is not None:
                self._banner.setVisible(False)


class _ReviewCard(QFrame):
    """One row of the metadata review queue with inline Approve / Skip buttons.

    Adapted from ``screens.metadata._ReviewCard`` — same per-card pick
    state machine. ``pick`` is one of ``""`` (undecided), ``"itunes"``
    (approve), or ``"skip"``. We keep ``"itunes"`` as the approve value
    regardless of which tier actually won the row because the merged-CSV
    writer mirrors the winning tier's data into the ``api_*`` columns, so
    a ``pick="itunes"`` approval correctly drives ``apply_approvals``.
    """

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

        self._approve_btn.setProperty("variant", "primary" if approved else None)
        self._approve_btn.style().unpolish(self._approve_btn)
        self._approve_btn.style().polish(self._approve_btn)

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


class _MetadataReviewPane(QWidget):
    """First approval gate — user reviews canonical metadata picks.

    Mirrors ``screens.metadata._ReviewPane``: stat tiles, bulk actions,
    a scrollable list of :class:`_ReviewCard`s, and a Cancel / Save &
    apply action row at the bottom.
    """

    save_clicked = Signal()
    cancel_clicked = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("Review metadata")
        title.setProperty("class", "h2")
        layout.addWidget(title)

        caption = QLabel(
            "Auto-apply matches are pre-approved; no-match rows are pre-skipped; "
            "review / low rows wait for your decision."
        )
        caption.setWordWrap(True)
        caption.setProperty("class", "muted")
        layout.addWidget(caption)

        # StatTile row: Total · Auto-approved · Needs review.
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(12)
        self._tile_total = StatTile("0", "Total")
        self._tile_auto = StatTile("0", "Auto-approved")
        self._tile_review = StatTile("0", "Needs review")
        for tile in (self._tile_total, self._tile_auto, self._tile_review):
            tiles_row.addWidget(tile, 1)
        layout.addLayout(tiles_row)

        # Bulk action row.
        bulk = QHBoxLayout()
        bulk.addStretch(1)
        self._approve_all = QPushButton("Approve all matches")
        self._approve_all.clicked.connect(self._on_approve_all)
        bulk.addWidget(self._approve_all)
        self._skip_all = QPushButton("Skip all")
        self._skip_all.clicked.connect(self._on_skip_all)
        bulk.addWidget(self._skip_all)
        layout.addLayout(bulk)

        # Scrollable card list.
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

        # Bottom action row.
        actions = QHBoxLayout()
        actions.addStretch(1)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.cancel_clicked)
        actions.addWidget(self._cancel_btn)
        self._save_btn = QPushButton("Save & apply →")
        self._save_btn.setProperty("variant", "commit")
        self._save_btn.clicked.connect(self.save_clicked)
        actions.addWidget(self._save_btn)
        layout.addLayout(actions)

        self._cards: list[_ReviewCard] = []

    def populate(self, rows: list[dict], bucket_counts: dict[str, int]) -> None:
        # Clear existing cards.
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

        # Order review > low > auto_apply > no_match so attention-needed rows top the list.
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
        # Save enabled iff no row is still undecided.
        self._save_btn.setEnabled(pending == 0)


class _PreviewPane(QWidget):
    """The middle pane while waiting for plan approval.

    Design centerpiece for the screen — the second approval gate. Layout
    follows the brief's "Plan preview" idiom: large h2 title, a row of
    three :class:`StatTile`, a :class:`Banner` declaring the apply mode +
    undo reassurance, then the planned-tree diff, then the action row
    with a default-secondary Cancel and a ``variant="commit"`` Apply.
    """

    apply_clicked = Signal()
    cancel_clicked = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        self._layout = layout

        title = QLabel("Plan preview")
        title.setProperty("class", "h2")
        layout.addWidget(title)

        # Stat tile row — total / primary country / duplicates.
        tiles_row = QHBoxLayout()
        tiles_row.setContentsMargins(0, 0, 0, 0)
        tiles_row.setSpacing(12)
        self._tile_total = StatTile("—", "Files planned")
        self._tile_country = StatTile("—", "Primary country")
        self._tile_dups = StatTile("—", "Duplicates quarantined")
        for tile in (self._tile_total, self._tile_country, self._tile_dups):
            tiles_row.addWidget(tile, 1)
        layout.addLayout(tiles_row)

        # Mode banner — slot owned so we can swap its body per populate().
        self._banner_slot_index = layout.count()
        self._banner: Banner | None = None

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Destination", "Files"])
        self.tree.setColumnWidth(0, 480)
        layout.addWidget(self.tree, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.cancel_clicked)
        actions.addWidget(self._cancel_btn)
        self._apply_btn = QPushButton("Apply →")
        self._apply_btn.setProperty("variant", "commit")
        self._apply_btn.clicked.connect(self.apply_clicked)
        actions.addWidget(self._apply_btn)
        layout.addLayout(actions)

    def _set_banner(self, mode_word: str) -> None:
        new_banner = Banner(
            severity="info",
            title=f"{mode_word} mode",
            body="An undo script will be written so you can revert this run.",
        )
        if self._banner is not None:
            self._layout.replaceWidget(self._banner, new_banner)
            self._banner.deleteLater()
        else:
            self._layout.insertWidget(self._banner_slot_index, new_banner)
        self._banner = new_banner

    def populate(self, *, stats: dict, mode: ApplyMode, plan_csv: Path, dups_csv: Path) -> None:
        total = int(stats.get("total", 0))
        by_country = stats.get("by_country", {}) or {}
        by_category = stats.get("by_category", {}) or {}

        # Tile values.
        self._tile_total.set_value(total)
        if by_country:
            top_country, top_count = max(by_country.items(), key=lambda kv: kv[1])
            self._tile_country.set_value(top_country or "—")
            self._tile_country.set_label(f"Primary country · {top_count:,} files")
        else:
            self._tile_country.set_value("—")
            self._tile_country.set_label("Primary country")

        mode_word = {"move": "Move", "copy": "Copy", "symlink": "Symlink"}[mode]
        self._set_banner(mode_word)

        # Update the commit button label with the actual planned count.
        if total:
            verb = {"move": "move", "copy": "copy", "symlink": "symlink"}[mode]
            self._apply_btn.setText(f"Apply → {verb} {total:,} files")
        else:
            self._apply_btn.setText("Apply →")
        self._apply_btn.style().unpolish(self._apply_btn)
        self._apply_btn.style().polish(self._apply_btn)

        # Category breakdown is no longer rendered as a sentence — the
        # tiles + tree carry the information now. Suppress unused.
        _ = by_category

        # Populate tree grouped by first segment under Music/
        self.tree.clear()
        by_group: dict[str, list[tuple[str, str]]] = {}
        if plan_csv.exists():
            with plan_csv.open() as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    src = row.get("src", "") or row.get("path", "")
                    dst = row.get("dst", "") or row.get("dest", "") or row.get("destination", "")
                    if not dst:
                        continue
                    group = self._group_key(dst)
                    by_group.setdefault(group, []).append((src, dst))

        dup_count = 0
        if dups_csv.exists():
            with dups_csv.open() as fh:
                dup_count = max(sum(1 for _ in fh) - 1, 0)
        self._tile_dups.set_value(dup_count)

        for group, items in sorted(by_group.items(), key=lambda kv: -len(kv[1])):
            top = QTreeWidgetItem(self.tree, [group, str(len(items))])
            for src, dst in items[:200]:
                QTreeWidgetItem(top, [Path(dst).name, ""]).setToolTip(0, f"{src}\n→ {dst}")
            if len(items) > 200:
                QTreeWidgetItem(top, [f"…+{len(items) - 200} more", ""])

        if dup_count:
            QTreeWidgetItem(self.tree, [f"_duplicates/ (quarantined duplicates)", str(dup_count)])

    @staticmethod
    def _group_key(dst: str) -> str:
        p = Path(dst)
        parts = p.parts
        if "Music" in parts:
            i = parts.index("Music")
            tail = parts[i + 1 : i + 4]
            if not tail:
                return "Music/"
            return "Music/" + "/".join(tail)
        return str(p.parent)


class _FailedPane(QWidget):
    back_clicked = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        self._layout = layout

        title = QLabel("Something went wrong")
        title.setProperty("class", "h2")
        layout.addWidget(title)

        # Banner replaces the inline error styling; rebuilt each show.
        self._banner_slot_index = layout.count()
        self._banner: Banner | None = None

        # Kept for public API compatibility — :meth:`PipelineScreen._show_failed_pane`
        # writes the message text here. We mirror it into the banner via
        # :meth:`set_message`.
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        layout.addWidget(self.message)

        layout.addStretch(1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        back_btn = QPushButton("← Back")
        back_btn.clicked.connect(self.back_clicked)
        actions.addWidget(back_btn)
        layout.addLayout(actions)

    def set_message(self, message: str) -> None:
        """Refresh both the hidden compat QLabel and the visible Banner."""
        self.message.setText(message)
        # Compose a title (first line or first 80 chars) and body.
        first_line = message.strip().splitlines()[0] if message.strip() else "Pipeline failed"
        if len(first_line) > 80:
            title_text = first_line[:77] + "…"
        else:
            title_text = first_line
        new_banner = Banner(severity="error", title=title_text, body=message)
        if self._banner is not None:
            self._layout.replaceWidget(self._banner, new_banner)
            self._banner.deleteLater()
        else:
            self._layout.insertWidget(self._banner_slot_index, new_banner)
        self._banner = new_banner


class PipelineScreen(QWidget):
    """One screen that owns the entire Stage 1 auto-flow."""

    completed = Signal(object)  # carries the execute stats dict
    cancelled = Signal()
    failed_out = Signal(str)

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg: Config | None = None
        self._root: Path | None = None
        self._mode: ApplyMode = "move"
        self._worker: LibraryWorker | None = None
        self._plan_stats: dict | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(16)

        self._title = QLabel("Organize library")
        self._title.setProperty("class", "h2")
        outer.addWidget(self._title)

        self._strip = _PhaseStrip()
        outer.addWidget(self._strip)

        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setStyleSheet("color: palette(mid);")
        outer.addWidget(rule)

        self._pane_stack = QStackedWidget()
        self._running_pane = _RunningPane()
        self._metadata_review_pane = _MetadataReviewPane()
        self._preview_pane = _PreviewPane()
        self._failed_pane = _FailedPane()
        for pane in (
            self._running_pane,
            self._metadata_review_pane,
            self._preview_pane,
            self._failed_pane,
        ):
            self._pane_stack.addWidget(pane)
        outer.addWidget(self._pane_stack, 1)

        self._metadata_review_pane.save_clicked.connect(self._on_metadata_save)
        self._metadata_review_pane.cancel_clicked.connect(self._on_cancel)
        self._preview_pane.apply_clicked.connect(self._on_apply)
        self._preview_pane.cancel_clicked.connect(self._on_cancel)
        self._failed_pane.back_clicked.connect(self._on_cancel)

    # ---- public API -----------------------------------------------------
    def start(self, cfg: Config, root: Path, mode: ApplyMode) -> None:
        self._cfg = cfg
        self._root = root
        self._mode = mode
        self._plan_stats = None
        self._title.setText(f"Organize library — {cfg.library_slug}")
        for key, _ in _PHASES:
            self._strip.set_status(key, "pending")
        self._running_pane.status.reset()
        self._show_running_pane()
        self._run_scan()

    # ---- pane switching --------------------------------------------------
    def _show_running_pane(self) -> None:
        self._pane_stack.setCurrentWidget(self._running_pane)

    def _show_metadata_review_pane(self) -> None:
        self._pane_stack.setCurrentWidget(self._metadata_review_pane)

    def _show_preview_pane(self) -> None:
        self._pane_stack.setCurrentWidget(self._preview_pane)

    def _show_failed_pane(self, message: str) -> None:
        self._failed_pane.set_message(message)
        self._pane_stack.setCurrentWidget(self._failed_pane)

    # ---- worker lifecycle ------------------------------------------------
    def _attach(self, worker: LibraryWorker, on_done: Any) -> None:
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.finished_with_result.connect(on_done)
        worker.failed.connect(self._on_worker_failed)
        worker.start()

    @Slot(object)
    def _on_progress(self, event: ProgressEvent) -> None:
        self._running_pane.status.push(event)

    @Slot(str)
    def _on_worker_failed(self, message: str) -> None:
        running = self._strip.running_phase()
        if running is not None:
            self._strip.set_status(running, "failed")
        self._show_failed_pane(message)
        self.failed_out.emit(message)

    # ---- phase chain -----------------------------------------------------
    def _run_scan(self) -> None:
        assert self._cfg and self._root is not None
        self._strip.set_status("scan", "running")
        self._running_pane.set_phase("scan")
        self._running_pane.headline.setText("Scanning your music folder…")
        self._attach(ScanWorker(self._cfg, self._root, parent=self), self._on_scan_done)

    @Slot(object)
    def _on_scan_done(self, _tracks: object) -> None:
        self._strip.set_status("scan", "done")
        self._run_canonicalize()

    # ---- metadata: lookup + review + apply --------------------------------
    def _run_canonicalize(self) -> None:
        assert self._cfg
        self._strip.set_status("metadata", "running")
        self._running_pane.set_phase("metadata")
        self._running_pane.headline.setText(
            "Looking up canonical metadata (JioSaavn → iTunes → Shazam)…"
        )
        self._show_running_pane()
        self._attach(
            CustomOrderCanonicalizeWorker(self._cfg, parent=self),
            self._on_canonicalize_done,
        )

    @Slot(object)
    def _on_canonicalize_done(self, stats: dict[str, int]) -> None:
        """Pivot to the review pane, OR fast-path apply if nothing to review."""
        assert self._cfg is not None
        rows = self._read_merged_csv()

        actionable = stats.get("auto_apply", 0) + stats.get("review", 0) + stats.get("low", 0)
        review_pending = stats.get("review", 0) + stats.get("low", 0)

        if actionable == 0:
            # Empty library or every row was no_match — nothing to tag/rename.
            # Skip the review pane and the apply worker entirely; move on to dedupe.
            self._strip.set_status("metadata", "done")
            self._run_dedupe()
            return

        if review_pending == 0:
            # Everything resolved to auto_apply or no_match — auto-build approvals
            # from auto_apply rows and apply without showing the review pane.
            picks = [
                {"source_path": r.get("source_path", ""), "pick": "itunes"}
                for r in rows if (r.get("decision") or "").lower() == "auto_apply"
            ]
            if not picks:
                self._strip.set_status("metadata", "done")
                self._run_dedupe()
                return
            self._write_approvals_and_apply(picks)
            return

        # User review needed.
        self._metadata_review_pane.populate(rows, stats)
        self._show_metadata_review_pane()

    def _read_merged_csv(self) -> list[dict]:
        assert self._cfg is not None
        merged = Path(self._cfg.state_dir) / "16_merged.csv"
        if not merged.exists():
            return []
        with merged.open(newline="") as fh:
            return list(csv.DictReader(fh))

    @Slot()
    def _on_metadata_save(self) -> None:
        picks = self._metadata_review_pane.picks()
        if not picks:
            # Every row skipped — nothing to apply. Advance straight to dedupe.
            self._strip.set_status("metadata", "done")
            self._show_running_pane()
            self._run_dedupe()
            return
        self._write_approvals_and_apply(picks)

    def _write_approvals_and_apply(self, picks: list[dict]) -> None:
        assert self._cfg is not None
        approvals_path = Path(self._cfg.state_dir) / "19_approvals.json"
        approvals_path.write_text(json.dumps(picks, indent=2), encoding="utf-8")

        self._running_pane.set_phase("metadata")
        self._running_pane.headline.setText("Applying canonical metadata…")
        self._show_running_pane()
        self._attach(
            ApplyApprovalsWorker(self._cfg, approvals_path, parent=self),
            self._on_apply_metadata_done,
        )

    @Slot(object)
    def _on_apply_metadata_done(self, _result: ApplyResult) -> None:
        self._strip.set_status("metadata", "done")
        self._run_dedupe()

    # ---- dedupe ---------------------------------------------------------
    def _run_dedupe(self) -> None:
        assert self._cfg
        self._strip.set_status("dedupe", "running")
        self._running_pane.set_phase("dedupe")
        self._running_pane.headline.setText("Finding duplicate audio files…")
        self._show_running_pane()
        self._attach(DedupeWorker(self._cfg, parent=self), self._on_dedupe_done)

    @Slot(object)
    def _on_dedupe_done(self, _outcome: DedupeOutcome) -> None:
        self._strip.set_status("dedupe", "done")
        self._run_resolve()

    # ---- resolve (internally folds into the "plan" user-facing pill) -----
    def _run_resolve(self) -> None:
        assert self._cfg
        # Plan pill turns on as soon as resolve begins — resolve + plan are
        # one user-facing phase.
        self._strip.set_status("plan", "running")
        self._running_pane.set_phase("plan")
        self._running_pane.headline.setText("Reconciling tags, folders, and filenames…")
        self._attach(ResolveWorker(self._cfg, parent=self), self._on_resolve_done)

    @Slot(object)
    def _on_resolve_done(self, _stats: object) -> None:
        # Stay on the plan pill — same user-facing phase.
        self._run_plan()

    def _run_plan(self) -> None:
        assert self._cfg
        self._running_pane.set_phase("plan")
        self._running_pane.headline.setText("Planning the destination tree…")
        self._attach(PlanWorker(self._cfg, parent=self), self._on_plan_done)

    @Slot(object)
    def _on_plan_done(self, stats: dict) -> None:
        self._strip.set_status("plan", "done")
        self._plan_stats = stats
        assert self._cfg
        state = Path(self._cfg.state_dir)
        self._preview_pane.populate(
            stats=stats,
            mode=self._mode,
            plan_csv=state / "09_plan.csv",
            dups_csv=state / "07_duplicates.csv",
        )
        self._show_preview_pane()

    @Slot()
    def _on_apply(self) -> None:
        self._show_running_pane()
        self._run_execute()

    @Slot()
    def _on_cancel(self) -> None:
        self.cancelled.emit()

    def _run_execute(self) -> None:
        assert self._cfg
        self._strip.set_status("execute", "running")
        self._running_pane.set_phase("execute")
        self._running_pane.headline.setText("Applying the plan…")
        self._attach(ExecuteWorker(self._cfg, self._mode, parent=self), self._on_execute_done)

    @Slot(object)
    def _on_execute_done(self, stats: dict) -> None:
        self._strip.set_status("execute", "done")
        self.completed.emit(stats)
