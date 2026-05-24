"""CompletionScreen — Stage 1 finished, library organized.

This is the natural landing page after the pipeline completes. From here
the user reaches the **separate functions** that are not part of the
auto-organize flow:

- **Retrieve metadata** (Stage 2: iTunes / JioSaavn / Shazam canonicalization)
- **Upgrade to lossless** (Stage 3: gamdl)
- **Browse undo history**
- **Organize another library**

Per [[feedback-auto-pipeline]] Stages 2 and 3 are independently
launched, not part of Stage 1; this screen is the entry point for them.
For the v0.3-gui MVP only the navigation paths are stubbed — clicking
Stage 2 / Stage 3 / Undo shows a "coming next" dialog.

Visual language follows the design brief: a big serif h1 title, a
muted summary line, a mono undo caption, a row of ``StatTile``s with
real numbers, the "What's next?" footnote, and four action tiles
styled with the paper-surface treatment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from musicorg import Config

from .. import theme as t
from ..widgets.design import StatTile


class _ActionTile(QFrame):
    """One large clickable tile for a post-org next step.

    Restyled to the design brief — paper surface, card-title sans for
    the heading, body class for the blurb, primary variant button only
    when this is the lead action (currently "Retrieve metadata").
    """

    clicked = Signal(str)

    def __init__(
        self,
        key: str,
        title: str,
        blurb: str,
        primary: bool = False,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._key = key
        self.setObjectName("actionTile")
        # Lead tile gets a stronger border to indicate the primary path.
        border = t.PRIMARY_500 if primary else t.BORDER_LIGHT
        self.setStyleSheet(
            f"#actionTile {{ background: {t.SURFACE_PAPER};"
            f" border: 1px solid {border};"
            f" border-radius: 16px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setProperty("class", "card-title")
        layout.addWidget(title_label)

        blurb_label = QLabel(blurb)
        blurb_label.setProperty("class", "body")
        blurb_label.setWordWrap(True)
        # Override colour to TEXT_MEDIUM so the blurb reads as supporting copy
        # under the card-title without overriding the body font/size.
        blurb_label.setStyleSheet(
            f"QLabel {{ color: {t.TEXT_MEDIUM}; background: transparent;"
            f" border: none; }}"
        )
        layout.addWidget(blurb_label, 1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.addStretch(1)
        self._btn = QPushButton("Open")
        if primary:
            self._btn.setProperty("variant", "primary")
        self._btn.style().unpolish(self._btn)
        self._btn.style().polish(self._btn)
        self._btn.clicked.connect(lambda: self.clicked.emit(self._key))
        btn_row.addWidget(self._btn)
        layout.addLayout(btn_row)


class CompletionScreen(QWidget):
    metadata_requested = Signal()
    upgrade_requested = Signal()
    undo_requested = Signal()
    restart_requested = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg: Config | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(64, 40, 64, 40)
        outer.setSpacing(14)

        # Heading -----------------------------------------------------------
        self._title = QLabel("Library organized")
        self._title.setProperty("class", "h1")
        outer.addWidget(self._title)

        self._summary = QLabel("")
        self._summary.setProperty("class", "muted")
        self._summary.setWordWrap(True)
        outer.addWidget(self._summary)

        self._undo_caption = QLabel("")
        self._undo_caption.setProperty("class", "mono")
        self._undo_caption.setWordWrap(True)
        outer.addWidget(self._undo_caption)

        outer.addSpacing(10)

        # Stat tiles --------------------------------------------------------
        self._stats_row = QHBoxLayout()
        self._stats_row.setSpacing(14)

        self._stat_files = StatTile("0", "Files moved")
        self._stat_dups = StatTile("0", "Duplicates quarantined")
        self._stat_phases = StatTile("0 / 7", "Phases done")

        self._stats_row.addWidget(self._stat_files, 1)
        self._stats_row.addWidget(self._stat_dups, 1)
        self._stats_row.addWidget(self._stat_phases, 1)
        outer.addLayout(self._stats_row)

        outer.addSpacing(8)

        # "What's next?" eyebrow + action tiles -----------------------------
        next_label = QLabel("What's next?")
        next_label.setProperty("class", "footnote")
        outer.addWidget(next_label)

        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(14)

        meta_tile = _ActionTile(
            "metadata",
            "Retrieve metadata",
            "Look up canonical title / artist / album / year via iTunes, "
            "JioSaavn, and Shazam. Rewrites tags with an undo script.",
            primary=True,
        )
        meta_tile.clicked.connect(self._on_metadata)
        tiles_row.addWidget(meta_tile, 1)

        upgrade_tile = _ActionTile(
            "upgrade",
            "Upgrade to lossless",
            "Replace lossy tracks with ALAC via gamdl. Needs Apple Music "
            "cookies and a Widevine device file (set up separately).",
        )
        upgrade_tile.clicked.connect(self._on_upgrade)
        tiles_row.addWidget(upgrade_tile, 1)
        outer.addLayout(tiles_row)

        tiles_row2 = QHBoxLayout()
        tiles_row2.setSpacing(14)

        undo_tile = _ActionTile(
            "undo",
            "Undo history",
            "Browse undo scripts and tag snapshots from this and prior runs. "
            "Revert any step.",
        )
        undo_tile.clicked.connect(self._on_undo)
        tiles_row2.addWidget(undo_tile, 1)

        another_tile = _ActionTile(
            "restart",
            "Organize another library",
            "Go back to the start. Each library keeps its own state directory "
            "so this doesn't touch the one you just finished.",
        )
        another_tile.clicked.connect(self._on_restart)
        tiles_row2.addWidget(another_tile, 1)
        outer.addLayout(tiles_row2)

        outer.addStretch(1)

    def _set_stats(
        self,
        files_value: str,
        files_label: str,
        dups_value: str,
        dups_label: str,
        phases_value: str,
        phases_label: str,
    ) -> None:
        self._stat_files.set_value(files_value)
        self._stat_files.set_label(files_label)
        self._stat_dups.set_value(dups_value)
        self._stat_dups.set_label(dups_label)
        self._stat_phases.set_value(phases_value)
        self._stat_phases.set_label(phases_label)

    def show_for(self, cfg: Config, execute_stats: dict) -> None:
        self._cfg = cfg
        moved = int(execute_stats.get("moved", 0))
        dup_moved = int(execute_stats.get("dup_moved", 0))
        noop = int(execute_stats.get("noop", 0))
        errors = int(execute_stats.get("errors", 0))
        mode = execute_stats.get("mode", cfg.apply_mode)

        bits = [f"{moved} files {mode}d"]
        if dup_moved:
            bits.append(f"{dup_moved} duplicates quarantined")
        if noop:
            bits.append(f"{noop} already in place")
        if errors:
            bits.append(f"{errors} errors")

        self._title.setText("Library organized")
        self._summary.setText(
            "  ·  ".join(bits)
            + f"   into {Path(cfg.library_root) / 'Music'}"
        )

        undo = execute_stats.get("undo_script")
        if undo:
            self._undo_caption.setText(f"Undo script written: {undo}")
        else:
            self._undo_caption.setText("")

        # Fresh-pipeline stats: files moved, duplicates quarantined,
        # phases done. Stage 1 covers phases 1-5; surface that as the
        # accurate "phases done" tile so the brief's 7-phase ring is
        # honest (Stages 2 and 3 still pending).
        self._set_stats(
            files_value=f"{moved:,}",
            files_label="Files moved",
            dups_value=f"{dup_moved:,}",
            dups_label="Duplicates quarantined",
            phases_value="5 / 7",
            phases_label="Phases done",
        )

    def show_existing(self, cfg: Config) -> None:
        """Land on Completion for a library opened from the recent list.

        Uses on-disk state for the summary — no fresh stats to render.
        """
        self._cfg = cfg
        state_dir = Path(cfg.state_dir)
        tags_csv = state_dir / "01_tags.csv"
        track_count = 0
        if tags_csv.exists():
            try:
                with tags_csv.open() as fh:
                    track_count = max(sum(1 for _ in fh) - 1, 0)
            except OSError:
                track_count = 0

        undo_scripts = sorted(state_dir.glob("undo_*.sh"))

        self._title.setText(f"Library — {cfg.library_slug}")
        bits = []
        if track_count:
            bits.append(f"{track_count} track{'s' if track_count != 1 else ''} scanned")
        if undo_scripts:
            bits.append(f"{len(undo_scripts)} prior organize run{'s' if len(undo_scripts) != 1 else ''}")
        self._summary.setText(
            "  ·  ".join(bits) + f"   at {cfg.library_root}"
            if bits else f"Opened library at {cfg.library_root}"
        )
        if undo_scripts:
            self._undo_caption.setText(f"Most recent undo: {undo_scripts[-1].name}")
        else:
            self._undo_caption.setText("")

        # For "opened from recent": show track count + prior-run count.
        # Replace the middle tile with a runs counter; the third tile
        # keeps the phases hint based on whether at least one run exists.
        phases_label = "7 / 7" if undo_scripts else "0 / 7"
        self._set_stats(
            files_value=f"{track_count:,}",
            files_label="Tracks scanned",
            dups_value=f"{len(undo_scripts):,}",
            dups_label="Prior organize runs",
            phases_value=phases_label,
            phases_label="Phases done",
        )

    @Slot(str)
    def _on_metadata(self, _key: str) -> None:
        self.metadata_requested.emit()

    @Slot(str)
    def _on_upgrade(self, _key: str) -> None:
        self.upgrade_requested.emit()

    @Slot(str)
    def _on_undo(self, _key: str) -> None:
        self.undo_requested.emit()

    @Slot(str)
    def _on_restart(self, _key: str) -> None:
        self.restart_requested.emit()
