"""Dashboard — per-phase status cards for the active library.

Reads on-disk state to compute each phase's status (Not run / In progress
/ Done) and renders one card per phase with a Run / Re-run button. The
state shape is the existing musicorg CSV layout under ``<state_dir>/``
documented in [README.md](README.md). For the MVP slice, only Scan is
wired to a working worker; the rest show an informative "coming next"
message when their Run button is pressed.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    QVBoxLayout,
    QWidget,
)

from musicorg import Config


@dataclass(frozen=True)
class _PhaseDef:
    key: str
    title: str
    description: str
    output_file: str  # relative to state_dir; non-empty existence = done


_PHASES: list[_PhaseDef] = [
    _PhaseDef("scan", "Scan", "Walk the music folder and read tags from every audio file.", "01_tags.csv"),
    _PhaseDef("dedupe", "Find duplicates", "Group near-identical files and pick a winner per group.", "07_winners.csv"),
    _PhaseDef("resolve", "Reconcile metadata", "Fuse folder name, tags, and filename into one canonical record.", "08_resolved.csv"),
    _PhaseDef("plan", "Plan moves", "Compute the destination tree under Music/<country>/<decade>/...", "09_plan.csv"),
    _PhaseDef("execute", "Apply moves", "Move, copy, or symlink files into the planned tree (with undo).", ""),
    _PhaseDef("canonicalize", "Canonical metadata", "Tiered iTunes → JioSaavn → Shazam lookup and tag rewrite.", "16_merged.csv"),
    _PhaseDef("upgrade", "Lossless upgrade", "Replace lossy files with ALAC via gamdl (needs setup).", ""),
]


class _PhaseCard(QFrame):
    """One row in the dashboard — title, status pill, description, Run button."""

    run_clicked = Signal(str)  # phase key

    def __init__(self, phase: _PhaseDef, parent: Any = None) -> None:
        super().__init__(parent)
        self._phase = phase
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { border: 1px solid palette(mid); border-radius: 6px; padding: 12px; }"
        )

        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title = QLabel(phase.title)
        title.setStyleSheet("font-size: 14px; font-weight: 600; border: none; padding: 0;")
        title_row.addWidget(title)

        self._status_pill = QLabel("Not run")
        self._status_pill.setStyleSheet(
            "QLabel { background: palette(mid); color: palette(window); "
            "border-radius: 8px; padding: 2px 8px; font-size: 11px; }"
        )
        title_row.addWidget(self._status_pill)
        title_row.addStretch(1)
        left.addLayout(title_row)

        desc = QLabel(phase.description)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: palette(mid); border: none; padding: 0;")
        left.addWidget(desc)

        outer.addLayout(left, 1)

        self._button = QPushButton("Run")
        self._button.clicked.connect(lambda: self.run_clicked.emit(phase.key))
        outer.addWidget(self._button, alignment=Qt.AlignmentFlag.AlignVCenter)

    def set_status(self, status: str, *, can_run: bool, button_label: str) -> None:
        self._status_pill.setText(status)
        color = {
            "Done": "#2e7d32",
            "Ready": "#1565c0",
            "Not run": "#616161",
            "Blocked": "#a16207",
        }.get(status, "#616161")
        self._status_pill.setStyleSheet(
            f"QLabel {{ background: {color}; color: white; border-radius: 8px;"
            f" padding: 2px 8px; font-size: 11px; }}"
        )
        self._button.setText(button_label)
        self._button.setEnabled(can_run)


class DashboardScreen(QWidget):
    """Phase-card dashboard for one open library."""

    run_requested = Signal(str)  # phase key — handled by MainWindow

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg: Config | None = None
        self._cards: dict[str, _PhaseCard] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(16)

        header_row = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        self._title = QLabel("Library")
        self._title.setStyleSheet("font-size: 22px; font-weight: 600;")
        title_block.addWidget(self._title)
        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet("color: palette(mid);")
        title_block.addWidget(self._subtitle)
        header_row.addLayout(title_block, 1)

        self._switch_btn = QPushButton("Switch library…")
        self._switch_btn.setEnabled(False)  # wired in the library-picker slice
        header_row.addWidget(self._switch_btn, alignment=Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(10)

        for phase in _PHASES:
            card = _PhaseCard(phase)
            card.run_clicked.connect(self._on_run)
            self._cards[phase.key] = card
            inner_layout.addWidget(card)

        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

    def set_library(self, cfg: Config) -> None:
        self._cfg = cfg
        self._title.setText(cfg.library_slug or "Library")
        self._subtitle.setText(f"{cfg.library_root}  ·  state: {cfg.state_dir}")
        self.refresh_status()

    def refresh_status(self) -> None:
        if self._cfg is None:
            return
        state_dir = Path(self._cfg.state_dir)
        prev_done = True
        for phase in _PHASES:
            card = self._cards[phase.key]
            done = bool(phase.output_file) and (state_dir / phase.output_file).exists()
            if done:
                card.set_status("Done", can_run=True, button_label="Re-run")
                prev_done = True
            elif prev_done:
                card.set_status("Ready", can_run=True, button_label="Run")
                prev_done = False
            else:
                card.set_status("Not run", can_run=False, button_label="Run")

    # Phases whose worker is wired and ready to drive from the dashboard.
    _IMPLEMENTED: frozenset[str] = frozenset({"dedupe"})

    @Slot(str)
    def _on_run(self, phase_key: str) -> None:
        if phase_key in self._IMPLEMENTED:
            self.run_requested.emit(phase_key)
            return
        phase = next((p for p in _PHASES if p.key == phase_key), None)
        title = phase.title if phase else phase_key
        QMessageBox.information(
            self,
            title,
            "This phase is coming in the next slice — the worker isn't wired yet.\n\n"
            "Order: scan → dedupe → resolve → plan → execute → canonicalize → upgrade.",
        )
