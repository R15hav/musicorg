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

from ..widgets import Banner, Pill, StatTile, StatusPanel
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


# Map blocker key -> (pill label when missing, pill label when ready).
_GATE_ROWS: tuple[tuple[str, str], ...] = (
    ("cookies", "Apple Music cookies.txt"),
    ("wvd", "Widevine .wvd device file"),
    ("gamdl", "gamdl on PATH"),
    ("candidates", "Apple Music URLs in state CSVs"),
)


class _GatePane(QWidget):
    """Shown when something the upgrade depends on isn't ready."""

    setup_requested = Signal()
    back_requested = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._title = QLabel("Lossless upgrade")
        self._title.setProperty("class", "h2")
        layout.addWidget(self._title)

        self._intro = QLabel(
            "Before the lossless upgrade can run, every prerequisite below "
            "needs to be ready."
        )
        self._intro.setWordWrap(True)
        self._intro.setProperty("class", "muted")
        layout.addWidget(self._intro)

        # Rows live on a paper surface for visual grouping.
        self._rows_frame = QFrame()
        self._rows_frame.setProperty("surface", "paper")
        rows_layout = QVBoxLayout(self._rows_frame)
        rows_layout.setContentsMargins(18, 14, 18, 14)
        rows_layout.setSpacing(8)

        self._row_widgets: dict[str, tuple[Pill, QLabel]] = {}
        for key, label in _GATE_ROWS:
            row = QHBoxLayout()
            row.setSpacing(12)
            pill = Pill("Missing", "block")
            row.addWidget(pill)
            text = QLabel(label)
            text.setProperty("class", "body")
            row.addWidget(text)
            row.addStretch(1)
            rows_layout.addLayout(row)
            self._row_widgets[key] = (pill, text)
        layout.addWidget(self._rows_frame)

        layout.addStretch(1)

        actions = QHBoxLayout()
        self._back_btn = QPushButton("← Back")
        self._back_btn.setProperty("variant", "ghost")
        self._back_btn.clicked.connect(self.back_requested)
        actions.addWidget(self._back_btn)
        actions.addStretch(1)
        self._setup_btn = QPushButton("Open gamdl setup")
        self._setup_btn.setProperty("variant", "primary")
        self._setup_btn.clicked.connect(self.setup_requested)
        actions.addWidget(self._setup_btn)
        layout.addLayout(actions)

    def show_status(
        self,
        statuses: dict[str, bool],
        has_setup_path: bool,
    ) -> None:
        """Repaint each gate row to ready / block based on statuses."""
        for key, (pill, _) in self._row_widgets.items():
            ok = statuses.get(key, False)
            if ok:
                pill.set_state("done", "Ready")
            else:
                pill.set_state("block", "Missing")
        self._setup_btn.setVisible(has_setup_path)


class _ConfirmPane(QWidget):
    """Shown when prerequisites are good — final go/no-go before the run."""

    start_clicked = Signal(bool)  # dry_run flag
    back_requested = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._title = QLabel("Lossless upgrade")
        self._title.setProperty("class", "h2")
        layout.addWidget(self._title)

        # Informational banner — track count + the gamdl latency warning.
        self._banner = Banner(
            severity="info",
            title="0 tracks will be upgraded",
            body=(
                "gamdl makes Apple Music requests; expect ~30 sec per track. "
                "Each successful upgrade replaces the lossy original — an "
                "undo script is written so the move is reversible."
            ),
        )
        layout.addWidget(self._banner)

        self._dry_run = QCheckBox(
            "Dry run (skip downloads — just print what would happen)"
        )
        layout.addWidget(self._dry_run)

        layout.addStretch(1)

        actions = QHBoxLayout()
        self._back_btn = QPushButton("← Back")
        self._back_btn.setProperty("variant", "ghost")
        self._back_btn.clicked.connect(self.back_requested)
        actions.addWidget(self._back_btn)
        actions.addStretch(1)
        self._start_btn = QPushButton("Start upgrade →")
        self._start_btn.setProperty("variant", "commit")
        self._start_btn.clicked.connect(
            lambda: self.start_clicked.emit(self._dry_run.isChecked())
        )
        actions.addWidget(self._start_btn)
        layout.addLayout(actions)

    def show_for(self, n_candidates: int) -> None:
        # Rebuild the banner with the right count. The Banner widget bakes
        # its title at construction so we keep one composer here and swap
        # the whole thing in place.
        parent_layout = self.layout()
        new_banner = Banner(
            severity="info",
            title=(
                f"{n_candidates} track{'s' if n_candidates != 1 else ''} "
                "will be upgraded"
            ),
            body=(
                "gamdl makes Apple Music requests; expect ~30 sec per track. "
                "Each successful upgrade replaces the lossy original — an "
                "undo script is written so the move is reversible."
            ),
        )
        parent_layout.replaceWidget(self._banner, new_banner)
        self._banner.setParent(None)
        self._banner.deleteLater()
        self._banner = new_banner
        self._dry_run.setChecked(False)


class _RunningPane(QWidget):
    """Pane shown while UpgradeWorker is in flight."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title = QLabel("Upgrading to lossless…")
        title.setProperty("class", "card-title")
        layout.addWidget(title)

        sub = QLabel(
            "Each track is downloaded via gamdl, then swapped into place. "
            "Skips are recorded to upgrade_skips.csv with their reason."
        )
        sub.setWordWrap(True)
        sub.setProperty("class", "muted")
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
        layout.setSpacing(12)

        self._title = QLabel("Upgrade complete")
        self._title.setProperty("class", "h2")
        layout.addWidget(self._title)

        # StatTile triplet
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(12)
        self._tile_upgraded = StatTile("0", "Upgraded")
        self._tile_permanent = StatTile("0", "Permanent skips")
        self._tile_transient = StatTile("0", "Transient failures")
        tiles_row.addWidget(self._tile_upgraded, 1)
        tiles_row.addWidget(self._tile_permanent, 1)
        tiles_row.addWidget(self._tile_transient, 1)
        layout.addLayout(tiles_row)

        # Skip-by-reason group lives on a paper surface.
        self._skips_frame = QFrame()
        self._skips_frame.setProperty("surface", "paper")
        self._skips_layout = QVBoxLayout(self._skips_frame)
        self._skips_layout.setContentsMargins(18, 14, 18, 14)
        self._skips_layout.setSpacing(8)
        self._skips_title = QLabel("")
        self._skips_title.setProperty("class", "footnote")
        self._skips_title.setVisible(False)
        self._skips_layout.addWidget(self._skips_title)
        self._skips_frame.setVisible(False)

        # Skip breakdown lives in a scroll area in case the taxonomy grows.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._skips_frame)
        layout.addWidget(scroll, 1)

        self._undo_caption = QLabel("")
        self._undo_caption.setWordWrap(True)
        self._undo_caption.setProperty("class", "caption")
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

        if was_dry_run:
            self._tile_upgraded.set_value(dry_run_count)
            self._tile_upgraded.set_label("Would upgrade")
        else:
            self._tile_upgraded.set_value(upgraded)
            self._tile_upgraded.set_label("Upgraded")
        self._tile_permanent.set_value(permanent)
        self._tile_transient.set_value(transient)

        # Rebuild skip breakdown rows.
        # Drop everything except the title label (index 0).
        while self._skips_layout.count() > 1:
            item = self._skips_layout.takeAt(1)
            w = item.widget() if item else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            else:
                lay = item.layout() if item else None
                if lay is not None:
                    # Recursively detach children of nested layouts.
                    while lay.count():
                        sub = lay.takeAt(0)
                        sw = sub.widget() if sub else None
                        if sw is not None:
                            sw.setParent(None)
                            sw.deleteLater()
        non_empty = [(reason, rows) for reason, rows in skip_report.items() if rows]
        if non_empty:
            self._skips_title.setText("Permanent skips by reason")
            self._skips_title.setVisible(True)
            self._skips_frame.setVisible(True)
            for reason, rows in non_empty:
                row = QHBoxLayout()
                row.setSpacing(10)
                pill = Pill("permanent", "block")
                row.addWidget(pill)
                reason_label = QLabel(reason)
                reason_label.setProperty("class", "body")
                row.addWidget(reason_label)
                row.addStretch(1)
                count_label = QLabel(str(len(rows)))
                count_label.setProperty("class", "caption")
                row.addWidget(count_label)
                self._skips_layout.addLayout(row)
        else:
            self._skips_title.setVisible(False)
            self._skips_frame.setVisible(False)

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
        layout.setSpacing(12)

        # The banner is the whole headline here; we still expose ``message`` so
        # the screen can call into it the way it always has.
        self._banner_holder = QVBoxLayout()
        layout.addLayout(self._banner_holder)
        self._banner: Banner | None = None
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        # Keep the QLabel alive (existing wiring writes to it) but render the
        # actual UI through the Banner widget we rebuild on each set.
        layout.addWidget(self.message)

        layout.addStretch(1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        back_btn = QPushButton("← Back")
        back_btn.clicked.connect(self.back_clicked)
        actions.addWidget(back_btn)
        layout.addLayout(actions)

    def set_message(self, text: str) -> None:
        # Keep the legacy attribute populated for anything that reads it.
        self.message.setText(text)
        new_banner = Banner(
            severity="error",
            title="Upgrade failed",
            body=text or "The upgrade worker reported a failure.",
        )
        if self._banner is not None:
            self._banner.setParent(None)
            self._banner.deleteLater()
        self._banner_holder.addWidget(new_banner)
        self._banner = new_banner


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
        cookies_ok = bool(
            cfg.gamdl_cookies_path and Path(cfg.gamdl_cookies_path).exists()
        )
        wvd_ok = bool(
            cfg.gamdl_wvd_path and Path(cfg.gamdl_wvd_path).exists()
        )
        gamdl_ok = shutil.which("gamdl") is not None
        candidates = _count_candidates(Path(cfg.state_dir))
        candidates_ok = candidates > 0

        statuses = {
            "cookies": cookies_ok,
            "wvd": wvd_ok,
            "gamdl": gamdl_ok,
            "candidates": candidates_ok,
        }

        prereqs_ok = cookies_ok and wvd_ok and gamdl_ok
        all_ok = prereqs_ok and candidates_ok

        if not all_ok:
            # If the only problem is "no candidates", setup screen won't help.
            has_setup_path = not prereqs_ok
            self._gate_pane.show_status(statuses, has_setup_path)
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
        self._failed_pane.set_message(message)
        self._stack.setCurrentWidget(self._failed_pane)
