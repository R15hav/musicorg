"""Reusable status panel — translated view by default, log view on toggle.

Replaces the inline ``QLabel`` + ``QProgressBar`` pair that every running
pane used to embed. Buffers up to 10,000 ``ProgressEvent``s in a ring
so the user can scroll back in log view without us holding the entire
session in memory.

Two views, same underlying event ring:

- **Translated** (default): one humanized line for the latest event,
  plus a progress bar. The phase-aware worker already sets
  ``ProgressEvent.message``; we just render it.
- **Log**: monospace, one line per event:
  ``HH:MM:SS  phase           message  [path]``. Auto-scrolls to the
  newest line; user can scroll back without it jumping.

The header has a small toggle button labelled "Log view" / "Status view"
plus a phase chip showing the current phase.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from musicorg import ProgressEvent


_MAX_BUFFER = 10_000


class StatusPanel(QWidget):
    """Drop-in replacement for the inline-status / progress-bar combo."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._buffer: Deque[tuple[datetime, ProgressEvent]] = deque(maxlen=_MAX_BUFFER)
        self._mode: str = "translated"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)

        self._phase_chip = QLabel("")
        self._phase_chip.setStyleSheet(
            "QLabel { background: palette(mid); color: palette(window);"
            " border-radius: 8px; padding: 2px 8px; font-size: 11px; }"
        )
        header.addWidget(self._phase_chip)
        header.addStretch(1)

        self._toggle_btn = QPushButton("Log view")
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        self._toggle_btn.clicked.connect(self._on_toggle)
        header.addWidget(self._toggle_btn)
        outer.addLayout(header)

        # ----- views (stacked) -----
        self._stack = QStackedWidget()

        translated = QWidget()
        tlayout = QVBoxLayout(translated)
        tlayout.setContentsMargins(0, 0, 0, 0)
        tlayout.setSpacing(8)

        self._status_line = QLabel("")
        self._status_line.setWordWrap(True)
        self._status_line.setStyleSheet("color: palette(mid);")
        tlayout.addWidget(self._status_line)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        tlayout.addWidget(self._progress)
        tlayout.addStretch(1)
        self._stack.addWidget(translated)

        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(10)
        self._log_view.setFont(mono)
        self._log_view.setStyleSheet(
            "QPlainTextEdit { border: 1px solid palette(mid); border-radius: 6px;"
            " background: palette(base); }"
        )
        self._stack.addWidget(self._log_view)

        outer.addWidget(self._stack, 1)
        self._set_phase_chip("idle")

    # ---- public API -----------------------------------------------------

    def reset(self) -> None:
        """Forget all buffered events. Use between phase runs."""
        self._buffer.clear()
        self._status_line.setText("")
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        self._log_view.clear()
        self._set_phase_chip("idle")

    @Slot(object)
    def push(self, event: ProgressEvent) -> None:
        """Append a ProgressEvent and update whichever view is active."""
        now = datetime.now()
        self._buffer.append((now, event))

        # Always update both views' state — they share an event ring.
        # The progress bar is bound to the *latest* event regardless of
        # whether the user is on log view or translated view.
        if event.total > 0:
            self._progress.setRange(0, event.total)
            self._progress.setValue(event.current)
        else:
            self._progress.setRange(0, 0)

        if event.message:
            self._status_line.setText(event.message)
        elif event.path:
            tail = Path(event.path).name
            self._status_line.setText(f"{event.phase}  {event.current}/{event.total}  {tail}")
        else:
            self._status_line.setText(f"{event.phase}  {event.current}/{event.total}")

        if event.phase:
            self._set_phase_chip(event.phase)

        # Append one line to the log view; auto-scroll if already at bottom.
        line = self._format_log_line(now, event)
        scrollbar = self._log_view.verticalScrollBar()
        at_bottom = scrollbar is not None and scrollbar.value() >= scrollbar.maximum() - 4
        self._log_view.appendPlainText(line)
        if at_bottom and scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    def set_progress_visible(self, visible: bool) -> None:
        """Hide the progress bar (e.g., on a final 'done' state)."""
        self._progress.setVisible(visible)

    # ---- internals ------------------------------------------------------

    def _set_phase_chip(self, phase: str) -> None:
        self._phase_chip.setText(phase)
        color = {
            "scan": "#1565c0",
            "dedupe": "#1565c0",
            "resolve": "#1565c0",
            "plan": "#1565c0",
            "execute": "#2e7d32",
            "undo": "#a16207",
        }.get(phase, "#616161")
        self._phase_chip.setStyleSheet(
            f"QLabel {{ background: {color}; color: white;"
            f" border-radius: 8px; padding: 2px 8px; font-size: 11px; }}"
        )

    @staticmethod
    def _format_log_line(when: datetime, event: ProgressEvent) -> str:
        ts = when.strftime("%H:%M:%S")
        phase = (event.phase or "-")[:12].ljust(12)
        level = "ERROR" if event.error else "INFO "
        bits = [event.message or ""]
        if event.path:
            bits.append(Path(event.path).name)
        if event.total > 0:
            bits.append(f"({event.current}/{event.total})")
        return f"{ts}  {phase}  {level}  {'  '.join(b for b in bits if b)}"

    @Slot()
    def _on_toggle(self) -> None:
        if self._mode == "translated":
            self._mode = "log"
            self._stack.setCurrentIndex(1)
            self._toggle_btn.setText("Status view")
        else:
            self._mode = "translated"
            self._stack.setCurrentIndex(0)
            self._toggle_btn.setText("Log view")
