"""Theme — typography + colors + Qt stylesheet for the design revamp.

The visual system is "CertifyOS — adapted" per the brief at
``docs/design/musicorg-design-brief.html`` (vendored from the Claude
Design app handoff). Token names mirror ``tokens.css`` from that bundle
so cross-referencing the brief stays mechanical.

Two fonts bundled under ``resources/fonts/`` are loaded at startup:

- **IvarText** (Commercial Type serif) — h1–h3 and big numbers in stat
  tiles.
- **ABCDiatype** (Dinamo grotesque) — body, UI, buttons, table cells.

Both fall back to system fonts if missing. JetBrains Mono is system-
provided (or whatever mono ships with the OS); we never bundle it.

Apply via :func:`apply_theme` from ``app.run()`` before MainWindow opens.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFile
from PySide6.QtGui import QColor, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication


# ---------------------------------------------------------------------------
# Tokens — copy of ``tokens.css`` :root values
# ---------------------------------------------------------------------------

# Neutrals
NEUTRAL_0 = "#FFFFFF"
NEUTRAL_50 = "#FAFAFA"
NEUTRAL_100 = "#F6F6F6"
NEUTRAL_200 = "#EEEEEE"
NEUTRAL_300 = "#E0E0E0"
NEUTRAL_400 = "#BDBDBD"
NEUTRAL_500 = "#9E9E9E"
NEUTRAL_600 = "#757575"
NEUTRAL_700 = "#616161"
NEUTRAL_800 = "#424242"
NEUTRAL_900 = "#212121"
NEUTRAL_950 = "#040610"

# Primary (warm yellow)
PRIMARY_50 = "#FFFDE7"
PRIMARY_100 = "#FFF9C4"
PRIMARY_200 = "#FFF38B"
PRIMARY_300 = "#FFEC6E"
PRIMARY_400 = "#F9D85B"
PRIMARY_500 = "#F3C948"  # brand main
PRIMARY_600 = "#ECB048"
PRIMARY_700 = "#E5974D"  # link / dark
PRIMARY_800 = "#C97A2E"
PRIMARY_900 = "#8A4F1A"

# Secondary (lavender / purple)
PURPLE_50 = "#F1ECFA"
PURPLE_100 = "#E9E3F5"
PURPLE_200 = "#D9D5F7"
PURPLE_300 = "#C9BFE9"
PURPLE_400 = "#A89DD4"
PURPLE_500 = "#79709E"  # mark, numerals
PURPLE_600 = "#5F578A"
PURPLE_700 = "#4A4374"
PURPLE_800 = "#2F2A52"

# Semantic
ERROR_BG = "#FEEBEE"
ERROR_MAIN = "#B00020"
ERROR_LIGHT = "#F44336"
SUCCESS_BG = "#E8F5E9"
SUCCESS_MAIN = "#4CAF50"
SUCCESS_LIGHT = "#AEDCAE"
SUCCESS_TEXT = "#1F6B26"
WARNING_BG = "#FFFDE7"
WARNING_ROW = "#FFF3E0"
WARNING_TEXT = "#8B5A0F"
INFO_BG = "#E3F2FD"
INFO_MAIN = "#1E88E5"
INFO_LIGHT = "#A8D4FF"
INFO_TEXT = "#1356A0"

# Surfaces
SURFACE_PAGE = NEUTRAL_100
SURFACE_PAPER = NEUTRAL_0
SURFACE_DARK = NEUTRAL_950

# Text
TEXT_HIGH = NEUTRAL_950
TEXT_MEDIUM = "rgba(0, 0, 0, 0.60)"
TEXT_DISABLED = "rgba(0, 0, 0, 0.38)"
TEXT_ON_PRIMARY = NEUTRAL_950
TEXT_LINK = PRIMARY_700

# Borders
BORDER_SUBTLE = NEUTRAL_100
BORDER_LIGHT = NEUTRAL_300
BORDER_MEDIUM = NEUTRAL_400


# ---------------------------------------------------------------------------
# Font setup
# ---------------------------------------------------------------------------

_FONTS_LOADED = False


def _resources_dir() -> Path:
    return Path(__file__).resolve().parent / "resources"


def load_fonts() -> None:
    """Register bundled fonts with QFontDatabase. Idempotent.

    Logs each load via QFile lookups; silent on missing files so the
    GUI still launches when the resources directory is incomplete
    (fall back chain in QSS picks up Inter / Source Serif Pro instead).
    """
    global _FONTS_LOADED
    if _FONTS_LOADED:
        return
    fonts_dir = _resources_dir() / "fonts"
    if not fonts_dir.exists():
        _FONTS_LOADED = True
        return
    for name in (
        "ABCDiatype-Regular.otf",
        "ABCDiatype-Medium.otf",
        "IvarText-Regular.otf",
        "IvarText-Italic.otf",
    ):
        path = fonts_dir / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    _FONTS_LOADED = True


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

# Font-family fallback chains mirror tokens.css.
SANS = "'ABCDiatype', 'Inter', 'Segoe UI', 'Helvetica Neue', sans-serif"
SERIF = "'IvarText', 'Source Serif Pro', 'Georgia', serif"
MONO = "'JetBrains Mono', 'SF Mono', 'Menlo', 'Consolas', monospace"


def stylesheet() -> str:
    """Return the global QSS for the application."""
    return f"""
    /* ───────────── Base ───────────── */
    QWidget {{
        background: {SURFACE_PAGE};
        color: {TEXT_HIGH};
        font-family: {SANS};
        font-size: 14px;
    }}

    QMainWindow, QDialog {{
        background: {SURFACE_PAGE};
    }}

    /* Paper surfaces — opt in via property("surface", "paper") on QFrame. */
    QFrame[surface="paper"] {{
        background: {SURFACE_PAPER};
        border: 1px solid {BORDER_LIGHT};
        border-radius: 16px;
    }}

    /* ───────────── Typography classes ─────────────
     * Set via QLabel.setProperty("class", "h1") / "h2" / etc.
     */
    QLabel[class="h1"] {{
        font-family: {SERIF};
        font-size: 48px;
        font-weight: 400;
        color: {TEXT_HIGH};
    }}
    QLabel[class="h2"] {{
        font-family: {SERIF};
        font-size: 32px;
        font-weight: 400;
        color: {TEXT_HIGH};
    }}
    QLabel[class="h3"] {{
        font-family: {SERIF};
        font-size: 24px;
        font-weight: 400;
        color: {TEXT_HIGH};
    }}
    QLabel[class="card-title"] {{
        font-family: {SANS};
        font-size: 18px;
        font-weight: 500;
        color: {TEXT_HIGH};
    }}
    QLabel[class="body"] {{
        font-family: {SANS};
        font-size: 14px;
        color: {TEXT_HIGH};
    }}
    QLabel[class="muted"] {{
        font-family: {SANS};
        font-size: 14px;
        color: {TEXT_MEDIUM};
    }}
    QLabel[class="caption"] {{
        font-family: {SANS};
        font-size: 12px;
        color: {TEXT_MEDIUM};
    }}
    QLabel[class="footnote"] {{
        font-family: {SANS};
        font-size: 11px;
        font-weight: 500;
        color: {PURPLE_500};
        text-transform: uppercase;
        letter-spacing: 1px;
    }}
    QLabel[class="mono"] {{
        font-family: {MONO};
        font-size: 12px;
        color: {TEXT_MEDIUM};
    }}
    QLabel[class="phase-num"] {{
        font-family: {SERIF};
        font-size: 44px;
        font-weight: 400;
        color: {PURPLE_500};
    }}
    QLabel[class="stat-value"] {{
        font-family: {SERIF};
        font-size: 40px;
        font-weight: 400;
        color: {TEXT_HIGH};
    }}
    QLabel[class="stat-label"] {{
        font-family: {SANS};
        font-size: 11px;
        font-weight: 500;
        color: {TEXT_MEDIUM};
        text-transform: uppercase;
        letter-spacing: 1px;
    }}

    /* ───────────── Buttons ───────────── */
    QPushButton {{
        font-family: {SANS};
        font-size: 13px;
        font-weight: 500;
        color: {TEXT_HIGH};
        background: {SURFACE_PAPER};
        border: 1px solid {BORDER_MEDIUM};
        border-radius: 8px;
        padding: 8px 14px;
        min-height: 18px;
    }}
    QPushButton:hover {{
        background: {NEUTRAL_50};
        border-color: {NEUTRAL_500};
    }}
    QPushButton:disabled {{
        color: {TEXT_DISABLED};
        background: {NEUTRAL_50};
        border-color: {BORDER_LIGHT};
    }}

    /* Primary — the brand yellow CTA. Use sparingly. */
    QPushButton[variant="primary"] {{
        background: {PRIMARY_500};
        color: {TEXT_ON_PRIMARY};
        border: 1px solid {PRIMARY_500};
    }}
    QPushButton[variant="primary"]:hover {{
        background: {PRIMARY_400};
        border-color: {PRIMARY_400};
    }}
    QPushButton[variant="primary"]:disabled {{
        background: {PRIMARY_200};
        border-color: {PRIMARY_200};
        color: {TEXT_DISABLED};
    }}

    /* Commit — the "you are writing to disk" button. Larger. */
    QPushButton[variant="commit"] {{
        background: {PRIMARY_500};
        color: {TEXT_ON_PRIMARY};
        border: 1px solid {PRIMARY_500};
        padding: 10px 22px;
        font-size: 14px;
        font-weight: 500;
    }}
    QPushButton[variant="commit"]:hover {{
        background: {PRIMARY_400};
    }}

    /* Outlined — secondary action with hierarchy under primary. */
    QPushButton[variant="outlined"] {{
        background: transparent;
        color: {PRIMARY_700};
        border: 1px solid {PRIMARY_700};
    }}
    QPushButton[variant="outlined"]:hover {{
        background: {PRIMARY_50};
    }}

    /* Ghost — minimal weight; cancel / dismiss. */
    QPushButton[variant="ghost"] {{
        background: transparent;
        border: 1px solid transparent;
        color: {TEXT_HIGH};
    }}
    QPushButton[variant="ghost"]:hover {{
        background: {NEUTRAL_100};
    }}

    /* Danger — destructive without confirmation. */
    QPushButton[variant="danger"] {{
        background: {ERROR_MAIN};
        color: {NEUTRAL_0};
        border: 1px solid {ERROR_MAIN};
    }}
    QPushButton[variant="danger"]:hover {{
        background: {ERROR_LIGHT};
    }}

    /* ───────────── Inputs ───────────── */
    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {SURFACE_PAPER};
        color: {TEXT_HIGH};
        border: 1px solid {BORDER_LIGHT};
        border-radius: 8px;
        padding: 6px 10px;
        selection-background-color: {PRIMARY_200};
        selection-color: {TEXT_HIGH};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {PRIMARY_500};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}

    /* ───────────── Radio buttons & checkboxes ───────────── */
    QRadioButton, QCheckBox {{
        font-family: {SANS};
        font-size: 13px;
        color: {TEXT_HIGH};
        spacing: 8px;
        background: transparent;
    }}
    QRadioButton::indicator, QCheckBox::indicator {{
        width: 16px;
        height: 16px;
    }}

    /* ───────────── ProgressBar — pill shape, yellow fill ───────────── */
    QProgressBar {{
        background: {NEUTRAL_200};
        border: none;
        border-radius: 4px;
        max-height: 8px;
        min-height: 8px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: {PRIMARY_500};
        border-radius: 4px;
    }}

    /* ───────────── Lists / trees ───────────── */
    QListWidget, QTreeWidget, QTreeView, QListView {{
        background: {SURFACE_PAPER};
        border: 1px solid {BORDER_LIGHT};
        border-radius: 8px;
        font-family: {SANS};
        font-size: 13px;
        color: {TEXT_HIGH};
        selection-background-color: {PURPLE_200};
        selection-color: {TEXT_HIGH};
        outline: 0;
    }}
    QListWidget::item, QTreeWidget::item {{
        padding: 6px 8px;
        border-bottom: 1px solid {BORDER_SUBTLE};
    }}
    QListWidget::item:selected, QTreeWidget::item:selected {{
        background: {PURPLE_200};
        color: {TEXT_HIGH};
    }}
    QHeaderView::section {{
        background: {NEUTRAL_100};
        color: {TEXT_MEDIUM};
        font-family: {SANS};
        font-size: 11px;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 1px;
        padding: 8px;
        border: none;
        border-bottom: 1px solid {BORDER_LIGHT};
    }}

    /* ───────────── Scrollbars ───────────── */
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 4px 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {NEUTRAL_300};
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {NEUTRAL_400};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 2px 4px;
    }}
    QScrollBar::handle:horizontal {{
        background: {NEUTRAL_300};
        border-radius: 4px;
        min-width: 30px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {NEUTRAL_400};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* ───────────── Tooltips ───────────── */
    QToolTip {{
        background: {NEUTRAL_950};
        color: {NEUTRAL_0};
        border: 1px solid {NEUTRAL_950};
        padding: 4px 8px;
        border-radius: 4px;
        font-family: {SANS};
        font-size: 12px;
    }}

    /* ───────────── Message boxes ───────────── */
    QMessageBox {{
        background: {SURFACE_PAPER};
    }}
    QMessageBox QLabel {{
        background: transparent;
        color: {TEXT_HIGH};
        font-family: {SANS};
        font-size: 14px;
    }}

    /* ───────────── Dialog ───────────── */
    QDialog {{
        background: {SURFACE_PAPER};
    }}
    """


def apply_theme(app: QApplication) -> None:
    """Install fonts + global stylesheet on the QApplication.

    Safe to call multiple times; font loading is idempotent.
    """
    load_fonts()
    app.setStyleSheet(stylesheet())

    # Also set a Qt palette so non-stylesheet-aware bits (native dialogs
    # in some platforms, scrollbar painting on Wayland) pick up the same
    # neutrals.
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(SURFACE_PAGE))
    pal.setColor(QPalette.ColorRole.Base, QColor(SURFACE_PAPER))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(NEUTRAL_50))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_HIGH))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT_HIGH))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(PURPLE_200))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(TEXT_HIGH))
    pal.setColor(QPalette.ColorRole.Link, QColor(TEXT_LINK))
    pal.setColor(QPalette.ColorRole.Button, QColor(SURFACE_PAPER))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_HIGH))
    app.setPalette(pal)
