"""Reusable design-language widgets — pills, phase cards, stat tiles, etc.

One-to-one translations of the components inventoried in the design
brief at ``docs/design/musicorg-design-brief.html``. All inherit the
global QSS in ``theme.py``; per-instance styling here is for variants
(pill state, primary vs secondary card, etc.) that QSS attribute
selectors can't easily reach.

Naming intent: these are widget-shaped components. Screens compose
them; they don't compose screens.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme as t


# ---------------------------------------------------------------------------
# Status pill — the brief calls for 6 states + dot + label
# ---------------------------------------------------------------------------

# (bg, fg, border) — matches brief.css .pill.* rules
_PILL_PALETTE: dict[str, tuple[str, str, str]] = {
    "not":   (t.NEUTRAL_100,  t.TEXT_MEDIUM,    t.NEUTRAL_200),
    "ready": (t.INFO_BG,      t.INFO_TEXT,      t.INFO_LIGHT),
    "run":   (t.PRIMARY_100,  t.PRIMARY_900,    t.PRIMARY_300),
    "done":  (t.SUCCESS_BG,   t.SUCCESS_TEXT,   t.SUCCESS_LIGHT),
    "block": (t.ERROR_BG,     t.ERROR_MAIN,     t.ERROR_LIGHT),
    "warn":  (t.WARNING_ROW,  t.WARNING_TEXT,   t.PRIMARY_300),
}


class Pill(QLabel):
    """Status pill — colored capsule with label.

    State is set via :meth:`set_state` taking one of:
    ``"not" | "ready" | "run" | "done" | "block" | "warn"``.
    The label string is whatever the caller passes; the leading
    coloured dot is appended automatically.
    """

    def __init__(self, label: str = "", state: str = "not", parent: Any = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._state = state
        self._label = label
        self._apply()

    def set_state(self, state: str, label: str | None = None) -> None:
        self._state = state
        if label is not None:
            self._label = label
        self._apply()

    def _apply(self) -> None:
        bg, fg, border = _PILL_PALETTE.get(self._state, _PILL_PALETTE["not"])
        self.setText(f"●  {self._label}")
        self.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {fg};"
            f" border: 1px solid {border};"
            f" border-radius: 12px; padding: 3px 12px;"
            f" font-family: {t.SANS}; font-size: 11px; font-weight: 500;"
            f" letter-spacing: 0.3px; }}"
        )


# ---------------------------------------------------------------------------
# Undo chip — persistent topbar element, "soft destructive" lavender
# ---------------------------------------------------------------------------


class UndoChip(QPushButton):
    """Pill-shaped button with a curved-arrow glyph + label.

    Used by the TopBar to surface that a destructive action is reversible.
    Emits ``clicked`` like any QPushButton — the consumer wires it to the
    undo browser.
    """

    def __init__(self, label: str = "No recent destructive action", parent: Any = None) -> None:
        super().__init__(parent)
        self._set_label(label)
        self._available = False
        self._apply()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_available(self, label: str) -> None:
        self._available = True
        self._set_label(label)
        self._apply()

    def set_empty(self) -> None:
        self._available = False
        self._set_label("No recent destructive action")
        self._apply()

    def _set_label(self, label: str) -> None:
        # Leading arrow glyph; couldn't bundle an SVG icon here, so use
        # a Unicode rotation arrow that reads as "undo" in most fonts.
        self.setText(f"⤺  {label}")

    def _apply(self) -> None:
        if self._available:
            bg = t.PURPLE_100
            fg = t.PURPLE_700
            border = t.PURPLE_300
        else:
            bg = t.SURFACE_PAPER
            fg = t.TEXT_MEDIUM
            border = t.BORDER_MEDIUM
        self.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: {fg};"
            f" border: 1px solid {border};"
            f" border-radius: 14px; padding: 5px 14px 5px 12px;"
            f" font-family: {t.SANS}; font-size: 12px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {t.PURPLE_200}; }}"
        )


# ---------------------------------------------------------------------------
# Phase card — large card with phase number, title, body, status, action
# ---------------------------------------------------------------------------


class PhaseCard(QFrame):
    """Brief-style phase card.

    Layout: ``[ num ] [ title + body + pill (stretches) ] [ controls col ]``.
    Use ``set_state`` to flip the pill and label.
    Use ``set_action(label, primary=)`` to set the right-side button.
    """

    action_clicked = Signal()

    def __init__(
        self,
        number: int,
        title: str,
        description: str,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("phaseCard")
        self.setStyleSheet(
            f"#phaseCard {{ background: {t.SURFACE_PAPER};"
            f" border: 1px solid {t.BORDER_LIGHT};"
            f" border-radius: 16px; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)

        num = QLabel(str(number))
        num.setProperty("class", "phase-num")
        num.setFixedWidth(48)
        num.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(num)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)

        title_label = QLabel(title)
        title_label.setProperty("class", "card-title")
        body.addWidget(title_label)

        desc = QLabel(description)
        desc.setProperty("class", "muted")
        desc.setWordWrap(True)
        body.addWidget(desc)

        self._pill = Pill("Not run", "not")
        pill_row = QHBoxLayout()
        pill_row.setContentsMargins(0, 6, 0, 0)
        pill_row.addWidget(self._pill)
        pill_row.addStretch(1)
        body.addLayout(pill_row)

        layout.addLayout(body, 1)

        controls = QVBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        self._btn = QPushButton("Run")
        self._btn.clicked.connect(self.action_clicked)
        controls.addWidget(self._btn)

        self._note = QLabel("")
        self._note.setProperty("class", "caption")
        controls.addWidget(self._note)

        layout.addLayout(controls)

    def set_state(self, state: str, label: str) -> None:
        self._pill.set_state(state, label)

    def set_action(self, label: str, *, primary: bool = False, enabled: bool = True) -> None:
        self._btn.setText(label)
        self._btn.setProperty("variant", "primary" if primary else None)
        self._btn.style().unpolish(self._btn)
        self._btn.style().polish(self._btn)
        self._btn.setEnabled(enabled)

    def set_note(self, note: str) -> None:
        self._note.setText(note)


# ---------------------------------------------------------------------------
# Stat tile — big-number serif + uppercase caption
# ---------------------------------------------------------------------------


class StatTile(QFrame):
    """One numerical KPI tile."""

    def __init__(self, value: str = "—", label: str = "", parent: Any = None) -> None:
        super().__init__(parent)
        self.setObjectName("statTile")
        self.setStyleSheet(
            f"#statTile {{ background: {t.SURFACE_PAPER};"
            f" border: 1px solid {t.BORDER_LIGHT};"
            f" border-radius: 16px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(4)

        self._value = QLabel(str(value))
        self._value.setProperty("class", "stat-value")
        layout.addWidget(self._value)

        self._label = QLabel(label)
        self._label.setProperty("class", "stat-label")
        layout.addWidget(self._label)

    def set_value(self, value: int | str) -> None:
        if isinstance(value, int):
            self._value.setText(f"{value:,}")
        else:
            self._value.setText(str(value))

    def set_label(self, label: str) -> None:
        self._label.setText(label)


# ---------------------------------------------------------------------------
# Banner — info / warning / error
# ---------------------------------------------------------------------------


_BANNER_PALETTE: dict[str, tuple[str, str, str]] = {
    "info":    (t.INFO_BG,     t.INFO_LIGHT,    t.INFO_MAIN),
    "warning": (t.WARNING_ROW, t.PRIMARY_300,   t.PRIMARY_700),
    "error":   (t.ERROR_BG,    t.ERROR_LIGHT,   t.ERROR_MAIN),
}


class Banner(QFrame):
    """Dockable banner above a screen's main content.

    Three severity levels (``info`` / ``warning`` / ``error``), each with a
    leading icon circle, bold title, and freeform body. No actions — the
    parent screen drops a button row underneath if needed (the brief
    shows banners both with and without action buttons).
    """

    def __init__(
        self,
        severity: str = "info",
        title: str = "",
        body: str = "",
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        bg, border, icon_bg = _BANNER_PALETTE.get(severity, _BANNER_PALETTE["info"])
        glyph = {"info": "i", "warning": "!", "error": "×"}.get(severity, "i")

        self.setObjectName("banner")
        self.setStyleSheet(
            f"#banner {{ background: {bg}; border: 1px solid {border};"
            f" border-radius: 10px; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        icon = QLabel(glyph)
        icon.setFixedSize(24, 24)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"QLabel {{ background: {icon_bg}; color: white;"
            f" border-radius: 12px;"
            f" font-family: {t.SERIF}; font-size: 14px; font-weight: 500;"
            f" border: none; }}"
        )
        layout.addWidget(icon, alignment=Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        if title:
            title_label = QLabel(title)
            title_label.setStyleSheet(
                f"QLabel {{ font-family: {t.SANS}; font-size: 13px;"
                f" font-weight: 500; color: {t.TEXT_HIGH};"
                f" background: transparent; border: none; }}"
            )
            text_col.addWidget(title_label)

        body_label = QLabel(body)
        body_label.setWordWrap(True)
        body_label.setStyleSheet(
            f"QLabel {{ font-family: {t.SANS}; font-size: 13px;"
            f" color: {t.TEXT_HIGH}; background: transparent; border: none; }}"
        )
        text_col.addWidget(body_label)
        layout.addLayout(text_col, 1)


# ---------------------------------------------------------------------------
# TopBar — persistent across all screens
# ---------------------------------------------------------------------------


class TopBar(QFrame):
    """Persistent top bar — brand mark, library name, undo chip, gear.

    Sits docked at the top of MainWindow above the QStackedWidget. The
    library name is hidden when no library is active. The undo chip is
    always present; its state changes via :meth:`set_undo`.
    """

    library_clicked = Signal()
    undo_clicked = Signal()
    settings_clicked = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setStyleSheet(
            f"#topBar {{ background: {t.SURFACE_PAPER};"
            f" border-bottom: 1px solid {t.BORDER_LIGHT}; }}"
        )
        self.setFixedHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 16, 0)
        layout.setSpacing(12)

        brand = QLabel("◆ musicorg")
        brand.setStyleSheet(
            f"QLabel {{ font-family: {t.SERIF}; font-size: 18px;"
            f" font-weight: 400; color: {t.PURPLE_500};"
            f" background: transparent; border: none; }}"
        )
        layout.addWidget(brand)

        self._library_label = QPushButton("")
        self._library_label.setVisible(False)
        self._library_label.setFlat(True)
        self._library_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._library_label.setStyleSheet(
            f"QPushButton {{ font-family: {t.SANS}; font-size: 13px;"
            f" font-weight: 500; color: {t.TEXT_HIGH};"
            f" background: transparent; border: none; padding: 4px 8px; }}"
            f"QPushButton:hover {{ color: {t.PURPLE_500}; }}"
        )
        self._library_label.clicked.connect(self.library_clicked)
        layout.addWidget(self._library_label)

        self._library_path = QLabel("")
        self._library_path.setStyleSheet(
            f"QLabel {{ font-family: {t.MONO}; font-size: 11px;"
            f" color: {t.TEXT_MEDIUM}; background: transparent; border: none; }}"
        )
        layout.addWidget(self._library_path)

        layout.addStretch(1)

        self._undo = UndoChip()
        self._undo.clicked.connect(self.undo_clicked)
        layout.addWidget(self._undo)

        self._settings = QPushButton("⚙")
        self._settings.setFlat(True)
        self._settings.setFixedSize(32, 32)
        self._settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none;"
            f" font-size: 18px; color: {t.TEXT_MEDIUM}; }}"
            f"QPushButton:hover {{ color: {t.TEXT_HIGH}; }}"
        )
        self._settings.clicked.connect(self.settings_clicked)
        layout.addWidget(self._settings)

    def set_library(self, slug: str | None, root: str | None = None) -> None:
        if slug:
            self._library_label.setText(f"{slug}  ▾")
            self._library_label.setVisible(True)
            if root:
                self._library_path.setText(root)
                self._library_path.setVisible(True)
        else:
            self._library_label.setVisible(False)
            self._library_path.setVisible(False)

    def set_undo(self, *, available: bool, label: str = "") -> None:
        if available:
            self._undo.set_available(label or "Undo available")
        else:
            self._undo.set_empty()
