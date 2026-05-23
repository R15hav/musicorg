"""DedupeResultsScreen — runs the DedupeWorker and shows the summary.

Two states in one screen:

- **Running**: progress bar + translated status line driven by
  ``DedupeWorker.progress``.
- **Done**: three big-number tiles (groups / winners / losers) plus a
  preview list of the first ~50 duplicate groups so the user can sanity-
  check what was picked. Read-only in the v0.3-gui MVP — winner-flipping
  arrives with the v0.4 dedupe review screen.

The "Back to dashboard" button is the natural Continue here since this
screen has no successor of its own — the next phase (resolve) is
launched from the Dashboard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from musicorg import Config, ProgressEvent

from ..workers import DedupeOutcome, DedupeWorker


class _StatTile(QFrame):
    """One big-number summary tile."""

    def __init__(self, label: str, parent: Any = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { border: 1px solid palette(mid); border-radius: 6px;"
            " padding: 16px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(4)

        self._number = QLabel("—")
        self._number.setStyleSheet("font-size: 28px; font-weight: 600; border: none; padding: 0;")
        layout.addWidget(self._number)

        caption = QLabel(label)
        caption.setStyleSheet("color: palette(mid); border: none; padding: 0;")
        layout.addWidget(caption)

    def set_value(self, value: int) -> None:
        self._number.setText(f"{value:,}")


class DedupeResultsScreen(QWidget):
    back_requested = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg: Config | None = None
        self._worker: DedupeWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(16)

        self._title = QLabel("Find duplicates")
        self._title.setStyleSheet("font-size: 22px; font-weight: 600;")
        outer.addWidget(self._title)

        self._status = QLabel("Preparing…")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: palette(mid);")
        outer.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        outer.addWidget(self._progress)

        # Summary tiles row, hidden until done.
        self._tiles_row = QHBoxLayout()
        self._tiles_row.setSpacing(12)
        self._groups_tile = _StatTile("groups")
        self._winners_tile = _StatTile("winners")
        self._losers_tile = _StatTile("duplicates")
        for tile in (self._groups_tile, self._winners_tile, self._losers_tile):
            self._tiles_row.addWidget(tile)
            tile.setVisible(False)
        outer.addLayout(self._tiles_row)

        self._preview_label = QLabel("First 50 duplicate groups (winner shown in bold):")
        self._preview_label.setVisible(False)
        outer.addWidget(self._preview_label)

        self._preview = QListWidget()
        self._preview.setVisible(False)
        self._preview.setStyleSheet("QListWidget { border: 1px solid palette(mid); border-radius: 6px; }")
        outer.addWidget(self._preview, 1)

        # Nav row, hidden until done.
        self._nav_row = QHBoxLayout()
        self._nav_row.addStretch(1)
        self._back_btn = QPushButton("← Back to dashboard")
        self._back_btn.clicked.connect(self.back_requested)
        self._back_btn.setVisible(False)
        self._nav_row.addWidget(self._back_btn)
        outer.addLayout(self._nav_row)

    def start(self, cfg: Config) -> None:
        """Kick off the dedupe worker for the given config."""
        self._cfg = cfg
        self._reset_to_running_state()

        self._worker = DedupeWorker(cfg, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_with_result.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _reset_to_running_state(self) -> None:
        self._status.setText("Starting…")
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        for tile in (self._groups_tile, self._winners_tile, self._losers_tile):
            tile.setVisible(False)
        self._preview_label.setVisible(False)
        self._preview.clear()
        self._preview.setVisible(False)
        self._back_btn.setVisible(False)

    @Slot(object)
    def _on_progress(self, event: ProgressEvent) -> None:
        if event.total > 0:
            self._progress.setRange(0, event.total)
            self._progress.setValue(event.current)
        if event.message:
            self._status.setText(event.message)

    @Slot(object)
    def _on_finished(self, outcome: DedupeOutcome) -> None:
        self._progress.setVisible(False)
        self._status.setText(
            f"Dedupe complete. Outputs in {Path(self._cfg.state_dir).name}/ — "
            "07_winners.csv, 07_duplicates.csv, 07_groups.csv."
        )

        self._groups_tile.set_value(len(outcome.groups))
        self._winners_tile.set_value(len(outcome.winners))
        self._losers_tile.set_value(len(outcome.losers))

        dup_groups = [g for g in outcome.groups if int(g.get("count", "0") or 0) > 1]
        for tile in (self._groups_tile, self._winners_tile, self._losers_tile):
            tile.setVisible(True)

        if dup_groups:
            self._preview_label.setText(
                f"First 50 of {len(dup_groups)} duplicate groups (winner highlighted):"
            )
            self._preview_label.setVisible(True)
            self._preview.setVisible(True)
            losers_by_group: dict[str, list[dict]] = {}
            for loser in outcome.losers:
                gid = loser.get("group_id", "")
                losers_by_group.setdefault(gid, []).append(loser)
            for group in dup_groups[:50]:
                gid = group.get("group_id", "")
                winner_title = group.get("title", "")
                winner_artist = group.get("artist", "")
                count = group.get("count", "?")
                item = QListWidgetItem(
                    f"★  {winner_title}  —  {winner_artist}   ({count} files)"
                )
                self._preview.addItem(item)
                for loser in losers_by_group.get(gid, []):
                    sub = QListWidgetItem(
                        f"      ↳ {Path(loser.get('path', '')).name}"
                    )
                    sub.setFlags(sub.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    self._preview.addItem(sub)

        self._back_btn.setVisible(True)
        self._back_btn.setFocus()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._progress.setVisible(False)
        self._status.setText(f"Dedupe failed: {message}")
        self._back_btn.setVisible(True)
