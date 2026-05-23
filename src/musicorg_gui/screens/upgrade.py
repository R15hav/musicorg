"""UpgradeScreen — Stage 3 (lossless ALAC upgrade) entry point.

Stage 3 is a **separate function** reached from CompletionScreen, not
part of the auto-pipeline. Because gamdl needs Apple Music cookies, a
Widevine device file, and a working ``gamdl`` binary before it can do
anything, this screen first sanity-checks that environment with a
"setup gate" pane:

- If cookies / wvd / gamdl are missing, or no candidates exist in the
  state CSVs, show the gate with an actionable explanation and a button
  to jump to :class:`GamdlSetupScreen`.
- Otherwise show a small confirm pane (track count, dry-run toggle),
  then a running pane streaming :class:`StatusPanel` events from
  :class:`UpgradeWorker`, then a done pane summarising the run via
  :func:`musicorg.permanent_skip_report`.
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from musicorg import Config, ProgressEvent, permanent_skip_report

from ..widgets import StatusPanel
from ..workers import LibraryWorker, UpgradeWorker


def _count_candidates(state_dir: Path) -> int:
    """Same logic as UpgradeWorker._build_candidates, but just counts."""
    for name in ("30_shazam_refingerprint.csv", "16_merged.csv"):
        csv_path = state_dir / name
        if not csv_path.exists():
            continue
        n = 0
        try:
            with csv_path.open(newline="") as fh:
                for row in csv.DictReader(fh):
                    url = (
                        (row.get("apple_music_url") or "").strip()
                        or (row.get("api_track_view_url") or "").strip()
                    )
                    if url:
                        n += 1
        except OSError:
            return 0
        return n
    return 0


class _GatePane(QWidget):
    """Shown when something the upgrade depends on isn't ready."""

    setup_requested = Signal()
    back_requested = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._title = QLabel("Lossless upgrade")
        self._title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(self._title)

        self._body = QLabel("")
        self._body.setWordWrap(True)
        self._body.setStyleSheet("color: palette(mid);")
        self._body.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._body)

        layout.addStretch(1)

        actions = QHBoxLayout()
        self._back_btn = QPushButton("← Back")
        self._back_btn.clicked.connect(self.back_requested)
        actions.addWidget(self._back_btn)
        actions.addStretch(1)
        self._setup_btn = QPushButton("Open gamdl setup")
        self._setup_btn.setStyleSheet("padding: 6px 16px; font-weight: 600;")
        self._setup_btn.clicked.connect(self.setup_requested)
        actions.addWidget(self._setup_btn)
        layout.addLayout(actions)

    def show_blockers(self, blockers: list[str], has_setup_path: bool) -> None:
        bullets = "".join(f"<li>{b}</li>" for b in blockers)
        self._body.setText(
            "Before the lossless upgrade can run, the following need "
            f"attention:<ul>{bullets}</ul>"
        )
        # If the only problem is "no candidates", the setup screen won't help.
        self._setup_btn.setVisible(has_setup_path)


class _ConfirmPane(QWidget):
    """Shown when prerequisites are good — final go/no-go before the run."""

    start_clicked = Signal(bool)  # dry_run flag
    back_requested = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._title = QLabel("Lossless upgrade")
        self._title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(self._title)

        self._count_label = QLabel("")
        self._count_label.setWordWrap(True)
        layout.addWidget(self._count_label)

        note = QLabel(
            "gamdl makes Apple Music download requests; expect roughly "
            "30 seconds per track. Each successful upgrade replaces the "
            "lossy original — an undo script is written so the move is "
            "reversible."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid); font-size: 12px;")
        layout.addWidget(note)

        self._dry_run = QCheckBox(
            "Dry run (skip downloads — just print what would happen)"
        )
        layout.addWidget(self._dry_run)

        layout.addStretch(1)

        actions = QHBoxLayout()
        self._back_btn = QPushButton("← Back")
        self._back_btn.clicked.connect(self.back_requested)
        actions.addWidget(self._back_btn)
        actions.addStretch(1)
        self._start_btn = QPushButton("Start upgrade →")
        self._start_btn.setStyleSheet("padding: 6px 16px; font-weight: 600;")
        self._start_btn.clicked.connect(
            lambda: self.start_clicked.emit(self._dry_run.isChecked())
        )
        actions.addWidget(self._start_btn)
        layout.addLayout(actions)

    def show_for(self, n_candidates: int) -> None:
        self._count_label.setText(
            f"<b>{n_candidates}</b> track{'s' if n_candidates != 1 else ''} "
            "will be upgraded to ALAC via gamdl."
        )
        self._dry_run.setChecked(False)


class _RunningPane(QWidget):
    """Pane shown while UpgradeWorker is in flight."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title = QLabel("Upgrading to lossless…")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(title)

        sub = QLabel(
            "Each track is downloaded via gamdl, then swapped into place. "
            "Skips are recorded to upgrade_skips.csv with their reason."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color: palette(mid);")
        layout.addWidget(sub)

        self.status = StatusPanel()
        layout.addWidget(self.status, 1)


class _DonePane(QWidget):
    """Pane shown after the worker completes (success or partial)."""

    back_clicked = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._title = QLabel("Upgrade complete")
        self._title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(self._title)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._skips_title = QLabel("")
        self._skips_title.setStyleSheet("font-weight: 600; margin-top: 8px;")
        self._skips_title.setVisible(False)
        layout.addWidget(self._skips_title)

        # Skip breakdown lives in a scroll area in case the taxonomy grows.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._skips_container = QWidget()
        self._skips_layout = QVBoxLayout(self._skips_container)
        self._skips_layout.setContentsMargins(0, 0, 0, 0)
        self._skips_layout.setSpacing(4)
        scroll.setWidget(self._skips_container)
        layout.addWidget(scroll, 1)

        self._undo_caption = QLabel("")
        self._undo_caption.setWordWrap(True)
        self._undo_caption.setStyleSheet("color: palette(mid); font-size: 12px;")
        layout.addWidget(self._undo_caption)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._back_btn = QPushButton("← Back")
        self._back_btn.clicked.connect(self.back_clicked)
        actions.addWidget(self._back_btn)
        layout.addLayout(actions)

    def show_for(
        self,
        summary: dict,
        skip_report: dict[str, list[dict]],
        *,
        was_dry_run: bool,
    ) -> None:
        counts = summary.get("counts", {}) if isinstance(summary, dict) else {}
        upgraded = int(counts.get("upgraded", 0))
        dry_run_count = int(counts.get("dry_run", 0))
        permanent = (
            int(counts.get("lossy_only_on_apple_music", 0))
            + int(counts.get("no_new_file", 0))
            + int(counts.get("skipped_url_normalize", 0))
        )
        transient = (
            int(counts.get("gamdl_failed", 0))
            + int(counts.get("original_missing", 0))
            + int(counts.get("target_collision", 0))
        )

        self._title.setText(
            "Upgrade complete (dry run)" if was_dry_run else "Upgrade complete"
        )

        bits: list[str] = []
        if was_dry_run:
            bits.append(f"{dry_run_count} would-upgrade")
        else:
            bits.append(f"{upgraded} upgraded")
        bits.append(f"{permanent} permanently skipped")
        bits.append(f"{transient} transient failures")
        self._summary.setText("  ·  ".join(bits))

        # Skip breakdown
        while self._skips_layout.count():
            item = self._skips_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        non_empty = [(reason, rows) for reason, rows in skip_report.items() if rows]
        if non_empty:
            self._skips_title.setText("Permanent skips by reason")
            self._skips_title.setVisible(True)
            for reason, rows in non_empty:
                line = QLabel(f"  {reason}: {len(rows)}")
                line.setStyleSheet("color: palette(mid); font-size: 12px;")
                self._skips_layout.addWidget(line)
            self._skips_layout.addStretch(1)
        else:
            self._skips_title.setVisible(False)

        undo_path = summary.get("undo_path") if isinstance(summary, dict) else ""
        if undo_path:
            self._undo_caption.setText(f"Undo script: {undo_path}")
        else:
            self._undo_caption.setText(
                "No successful upgrades — nothing to undo."
            )


class _FailedPane(QWidget):
    back_clicked = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title = QLabel("Upgrade failed")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
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


class UpgradeScreen(QWidget):
    """Stage 3 entry point — runs UpgradeWorker after a setup gate."""

    back_requested = Signal()
    setup_requested = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg: Config | None = None
        self._worker: LibraryWorker | None = None
        self._dry_run: bool = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 32, 48, 32)
        outer.setSpacing(16)

        self._stack = QStackedWidget(self)
        self._gate_pane = _GatePane()
        self._confirm_pane = _ConfirmPane()
        self._running_pane = _RunningPane()
        self._done_pane = _DonePane()
        self._failed_pane = _FailedPane()
        for pane in (
            self._gate_pane,
            self._confirm_pane,
            self._running_pane,
            self._done_pane,
            self._failed_pane,
        ):
            self._stack.addWidget(pane)
        outer.addWidget(self._stack, 1)

        self._gate_pane.setup_requested.connect(self.setup_requested)
        self._gate_pane.back_requested.connect(self.back_requested)
        self._confirm_pane.back_requested.connect(self.back_requested)
        self._confirm_pane.start_clicked.connect(self._on_start)
        self._done_pane.back_clicked.connect(self.back_requested)
        self._failed_pane.back_clicked.connect(self.back_requested)

    # ---- public API ----
    def show_for(self, cfg: Config) -> None:
        self._cfg = cfg
        blockers: list[str] = []
        has_setup_path = False

        if not cfg.gamdl_cookies_path or not Path(cfg.gamdl_cookies_path).exists():
            blockers.append(
                "Apple Music <b>cookies.txt</b> is not configured. "
                "Set it up in the gamdl setup screen."
            )
            has_setup_path = True
        if not cfg.gamdl_wvd_path or not Path(cfg.gamdl_wvd_path).exists():
            blockers.append(
                "Widevine <b>.wvd</b> device file is not configured. "
                "Set it up in the gamdl setup screen."
            )
            has_setup_path = True
        if shutil.which("gamdl") is None:
            blockers.append(
                "<b>gamdl</b> is not on your PATH. Install it from the "
                "gamdl setup screen."
            )
            has_setup_path = True

        candidates = _count_candidates(Path(cfg.state_dir))
        if not blockers and candidates == 0:
            blockers.append(
                "No Apple Music URLs found in 16_merged.csv. Run "
                "<b>Retrieve metadata</b> first so the lookup chain can "
                "harvest URLs for the upgrade step."
            )
            has_setup_path = False

        if blockers:
            self._gate_pane.show_blockers(blockers, has_setup_path)
            self._stack.setCurrentWidget(self._gate_pane)
            return

        self._confirm_pane.show_for(candidates)
        self._stack.setCurrentWidget(self._confirm_pane)

    # ---- start the worker ----
    @Slot(bool)
    def _on_start(self, dry_run: bool) -> None:
        assert self._cfg is not None
        self._dry_run = dry_run
        self._running_pane.status.reset()
        self._stack.setCurrentWidget(self._running_pane)

        self._worker = UpgradeWorker(self._cfg, dry_run=dry_run, parent=self)
        self._worker.progress.connect(self._running_pane.status.push)
        self._worker.finished_with_result.connect(self._on_upgrade_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    @Slot(object)
    def _on_upgrade_done(self, summary: object) -> None:
        assert self._cfg is not None
        if not isinstance(summary, dict):
            summary = {}
        skip_report = permanent_skip_report(Path(self._cfg.state_dir))
        self._done_pane.show_for(
            summary,
            skip_report,
            was_dry_run=self._dry_run,
        )
        self._running_pane.status.set_progress_visible(False)
        self._stack.setCurrentWidget(self._done_pane)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._failed_pane.message.setText(message)
        self._stack.setCurrentWidget(self._failed_pane)
