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
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from musicorg import Config


class _ActionTile(QFrame):
    """One large clickable tile for a post-org next step."""

    clicked = Signal(str)

    def __init__(self, key: str, title: str, blurb: str, primary: bool = False, parent: Any = None) -> None:
        super().__init__(parent)
        self._key = key
        self.setFrameShape(QFrame.Shape.StyledPanel)
        border = "#1565c0" if primary else "palette(mid)"
        self.setStyleSheet(
            f"QFrame {{ border: 1px solid {border}; border-radius: 8px; padding: 16px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(6)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 15px; font-weight: 600; border: none; padding: 0;")
        layout.addWidget(title_label)

        blurb_label = QLabel(blurb)
        blurb_label.setWordWrap(True)
        blurb_label.setStyleSheet("color: palette(mid); border: none; padding: 0;")
        layout.addWidget(blurb_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn = QPushButton("Open")
        self._btn.setStyleSheet("padding: 4px 14px;" + (" font-weight: 600;" if primary else ""))
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
        outer.setContentsMargins(48, 32, 48, 32)
        outer.setSpacing(16)

        self._title = QLabel("Library organized")
        self._title.setStyleSheet("font-size: 24px; font-weight: 600;")
        outer.addWidget(self._title)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet("color: palette(mid);")
        outer.addWidget(self._summary)

        self._undo_caption = QLabel("")
        self._undo_caption.setWordWrap(True)
        self._undo_caption.setStyleSheet("color: palette(mid); font-size: 12px;")
        outer.addWidget(self._undo_caption)

        next_label = QLabel("What's next?")
        next_label.setStyleSheet("font-weight: 600; margin-top: 12px;")
        outer.addWidget(next_label)

        # Two-column grid of tiles
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(12)

        meta_tile = _ActionTile(
            "metadata",
            "Retrieve metadata",
            "Look up canonical title / artist / album / year via iTunes, "
            "JioSaavn, and Shazam. Rewrites tags with an undo script.",
            primary=True,
        )
        meta_tile.clicked.connect(self._on_metadata)
        tiles_row.addWidget(meta_tile)

        upgrade_tile = _ActionTile(
            "upgrade",
            "Upgrade to lossless",
            "Replace lossy tracks with ALAC via gamdl. Needs Apple Music "
            "cookies and a Widevine device file (set up separately).",
        )
        upgrade_tile.clicked.connect(self._on_upgrade)
        tiles_row.addWidget(upgrade_tile)
        outer.addLayout(tiles_row)

        tiles_row2 = QHBoxLayout()
        tiles_row2.setSpacing(12)

        undo_tile = _ActionTile(
            "undo",
            "Undo history",
            "Browse undo scripts and tag snapshots from this and prior runs. "
            "Revert any step.",
        )
        undo_tile.clicked.connect(self._on_undo)
        tiles_row2.addWidget(undo_tile)

        another_tile = _ActionTile(
            "restart",
            "Organize another library",
            "Go back to the start. Each library keeps its own state directory "
            "so this doesn't touch the one you just finished.",
        )
        another_tile.clicked.connect(self._on_restart)
        tiles_row2.addWidget(another_tile)
        outer.addLayout(tiles_row2)

        outer.addStretch(1)

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
