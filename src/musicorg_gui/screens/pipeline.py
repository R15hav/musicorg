"""PipelineScreen — auto-runs Stage 1 with one approval gate at plan preview.

Flow:

    scan → dedupe → resolve → plan  →  [user approves]  →  execute → done

A horizontal phase strip at the top shows where the pipeline is. The
middle pane shows either:

- **Running state**: current phase, translated status line, progress bar.
- **Plan preview**: summary tiles + per-destination breakdown + Apply /
  Cancel buttons. The single approval gate.
- **Failed state**: error message + Back button.

Emits ``completed(stats)`` when Execute finishes successfully so
MainWindow can transition to CompletionScreen. ``cancelled()`` fires on
user Cancel, ``failed(message)`` on any worker exception.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from musicorg import Config, ProgressEvent

from ..workers import (
    ApplyMode,
    DedupeOutcome,
    DedupeWorker,
    ExecuteWorker,
    LibraryWorker,
    PlanWorker,
    ResolveWorker,
    ScanWorker,
)


_PHASES: list[tuple[str, str]] = [
    ("scan", "Scan"),
    ("dedupe", "Dedupe"),
    ("resolve", "Resolve"),
    ("plan", "Plan"),
    ("execute", "Apply"),
]


class _PhaseStrip(QWidget):
    """Horizontal pill row showing per-phase status (pending / running / done)."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._pills: dict[str, QLabel] = {}
        for i, (key, label) in enumerate(_PHASES):
            pill = QLabel(f"  ○  {label}  ")
            pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._pills[key] = pill
            layout.addWidget(pill)
            if i < len(_PHASES) - 1:
                sep = QLabel("→")
                sep.setStyleSheet("color: palette(mid);")
                layout.addWidget(sep)
        layout.addStretch(1)
        self.set_status("scan", "pending")  # initialize all
        for key, _ in _PHASES:
            self.set_status(key, "pending")

    def set_status(self, phase: str, status: str) -> None:
        pill = self._pills.get(phase)
        if pill is None:
            return
        icon = {"pending": "○", "running": "●", "done": "✓", "failed": "✕"}[status]
        label = dict(_PHASES)[phase]
        pill.setText(f"  {icon}  {label}  ")
        color, bg = {
            "pending": ("palette(mid)", "transparent"),
            "running": ("white", "#1565c0"),
            "done": ("white", "#2e7d32"),
            "failed": ("white", "#c62828"),
        }[status]
        pill.setStyleSheet(
            f"QLabel {{ color: {color}; background: {bg};"
            f" border-radius: 10px; padding: 4px 10px; font-size: 12px; }}"
        )


class _RunningPane(QWidget):
    """The middle pane while a worker is active."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.headline = QLabel("Preparing…")
        self.headline.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(self.headline)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)

        layout.addStretch(1)


class _PreviewPane(QWidget):
    """The middle pane while waiting for plan approval."""

    apply_clicked = Signal()
    cancel_clicked = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title = QLabel("Plan preview")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._mode_caption = QLabel("")
        self._mode_caption.setStyleSheet("color: palette(mid); font-size: 12px;")
        layout.addWidget(self._mode_caption)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Destination", "Files"])
        self.tree.setStyleSheet(
            "QTreeWidget { border: 1px solid palette(mid); border-radius: 6px; }"
        )
        self.tree.setColumnWidth(0, 480)
        layout.addWidget(self.tree, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.cancel_clicked)
        actions.addWidget(self._cancel_btn)
        self._apply_btn = QPushButton("Apply →")
        self._apply_btn.setStyleSheet("padding: 6px 16px; font-weight: 600;")
        self._apply_btn.clicked.connect(self.apply_clicked)
        actions.addWidget(self._apply_btn)
        layout.addLayout(actions)

    def populate(self, *, stats: dict, mode: ApplyMode, plan_csv: Path, dups_csv: Path) -> None:
        total = int(stats.get("total", 0))
        by_country = stats.get("by_country", {}) or {}
        by_category = stats.get("by_category", {}) or {}

        bits = []
        for country, count in sorted(by_country.items(), key=lambda kv: -kv[1]):
            bits.append(f"{count} {country}")
        category_bits = []
        for category, count in sorted(by_category.items(), key=lambda kv: -kv[1]):
            category_bits.append(f"{count} {category.replace('_', ' ')}")

        sentence = f"{total} files planned"
        if bits:
            sentence += f" — {', '.join(bits)}"
        if category_bits:
            sentence += f" ({', '.join(category_bits)})"
        self._summary.setText(sentence)

        mode_word = {"move": "Move", "copy": "Copy", "symlink": "Symlink"}[mode]
        self._mode_caption.setText(
            f"{mode_word} mode. An undo script will be written so you can revert this run."
        )

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
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title = QLabel("Something went wrong")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        layout.addStretch(1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        back_btn = QPushButton("← Back")
        back_btn.clicked.connect(self.back_clicked)
        actions.addWidget(back_btn)
        layout.addLayout(actions)


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
        self._title.setStyleSheet("font-size: 22px; font-weight: 600;")
        outer.addWidget(self._title)

        self._strip = _PhaseStrip()
        outer.addWidget(self._strip)

        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setStyleSheet("color: palette(mid);")
        outer.addWidget(rule)

        self._pane_stack = QStackedWidget()
        self._running_pane = _RunningPane()
        self._preview_pane = _PreviewPane()
        self._failed_pane = _FailedPane()
        for pane in (self._running_pane, self._preview_pane, self._failed_pane):
            self._pane_stack.addWidget(pane)
        outer.addWidget(self._pane_stack, 1)

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
        self._show_running_pane()
        self._run_scan()

    # ---- pane switching --------------------------------------------------
    def _show_running_pane(self) -> None:
        self._pane_stack.setCurrentWidget(self._running_pane)

    def _show_preview_pane(self) -> None:
        self._pane_stack.setCurrentWidget(self._preview_pane)

    def _show_failed_pane(self, message: str) -> None:
        self._failed_pane.message.setText(message)
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
        if event.total > 0:
            self._running_pane.progress.setRange(0, event.total)
            self._running_pane.progress.setValue(event.current)
        else:
            self._running_pane.progress.setRange(0, 0)
        if event.message:
            self._running_pane.status.setText(event.message)
        elif event.path:
            tail = Path(event.path).name
            self._running_pane.status.setText(f"{event.phase}  {event.current}/{event.total}  {tail}")

    @Slot(str)
    def _on_worker_failed(self, message: str) -> None:
        for key, _ in _PHASES:
            # Mark whatever was running as failed
            pill = self._strip._pills[key]
            if "●" in pill.text():
                self._strip.set_status(key, "failed")
        self._show_failed_pane(message)
        self.failed_out.emit(message)

    # ---- phase chain -----------------------------------------------------
    def _run_scan(self) -> None:
        assert self._cfg and self._root is not None
        self._strip.set_status("scan", "running")
        self._running_pane.headline.setText("Scanning your music folder…")
        self._running_pane.status.setText("")
        self._attach(ScanWorker(self._cfg, self._root, parent=self), self._on_scan_done)

    @Slot(object)
    def _on_scan_done(self, _tracks: object) -> None:
        self._strip.set_status("scan", "done")
        self._run_dedupe()

    def _run_dedupe(self) -> None:
        assert self._cfg
        self._strip.set_status("dedupe", "running")
        self._running_pane.headline.setText("Finding duplicate audio files…")
        self._running_pane.status.setText("")
        self._attach(DedupeWorker(self._cfg, parent=self), self._on_dedupe_done)

    @Slot(object)
    def _on_dedupe_done(self, _outcome: DedupeOutcome) -> None:
        self._strip.set_status("dedupe", "done")
        self._run_resolve()

    def _run_resolve(self) -> None:
        assert self._cfg
        self._strip.set_status("resolve", "running")
        self._running_pane.headline.setText("Reconciling tags, folders, and filenames…")
        self._running_pane.status.setText("")
        self._attach(ResolveWorker(self._cfg, parent=self), self._on_resolve_done)

    @Slot(object)
    def _on_resolve_done(self, _stats: object) -> None:
        self._strip.set_status("resolve", "done")
        self._run_plan()

    def _run_plan(self) -> None:
        assert self._cfg
        self._strip.set_status("plan", "running")
        self._running_pane.headline.setText("Planning the destination tree…")
        self._running_pane.status.setText("")
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
        self._running_pane.headline.setText("Applying the plan…")
        self._running_pane.status.setText("")
        self._attach(ExecuteWorker(self._cfg, self._mode, parent=self), self._on_execute_done)

    @Slot(object)
    def _on_execute_done(self, stats: dict) -> None:
        self._strip.set_status("execute", "done")
        self.completed.emit(stats)
