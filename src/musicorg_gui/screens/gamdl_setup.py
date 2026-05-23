"""GamdlSetupScreen — dedicated walkthrough for Stage 3 prerequisites.

gamdl needs three things before it can pull ALAC originals:

1. **Apple Music cookies** — exported from a signed-in browser session
   (Netscape format, via the Cookie-Editor extension).
2. **A Widevine device file** (``.wvd``) from an L3 CDM dump — we
   cannot host or distribute these; see the gamdl README.
3. **gamdl itself**, installed and on ``PATH``.

Each step shows a coloured status dot, a brief blurb, and an inline
control. Step 4 is a roll-up: green check when all three steps are
ready, otherwise lists what's still missing. The Continue button is
disabled until every step is green.

Settings persist to the **global** musicorg config (``[gamdl]`` section)
via :func:`musicorg.save_global_config` — they're per-machine, not
per-library. The screen re-reads from disk on every ``show_for`` call so
it always reflects the current state.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from musicorg import Config, save_global_config


_DOT_OK = "#2e7d32"
_DOT_MISSING = "#c62828"
_DOT_MUTED = "#616161"


def _path_is_readable(p: str) -> bool:
    if not p:
        return False
    try:
        path = Path(p)
        return path.exists() and path.is_file()
    except OSError:
        return False


def _gamdl_version() -> str | None:
    """Return the first line of ``gamdl --version`` or None on failure."""
    if shutil.which("gamdl") is None:
        return None
    try:
        proc = subprocess.run(
            ["gamdl", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (proc.stdout or proc.stderr or "").strip().splitlines()
    return out[0] if out else "gamdl (version unknown)"


class _StepCard(QFrame):
    """One step in the walkthrough — title, status pill, blurb, controls."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { border: 1px solid palette(mid); border-radius: 6px;"
            " padding: 10px; }"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 12)
        outer.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._title = QLabel("")
        self._title.setStyleSheet(
            "font-weight: 600; border: none; padding: 0; font-size: 14px;"
        )
        header.addWidget(self._title)
        header.addStretch(1)
        self._status_pill = QLabel("")
        self._status_pill.setStyleSheet(
            f"QLabel {{ background: {_DOT_MUTED}; color: white;"
            f" border-radius: 8px; padding: 1px 8px; font-size: 11px;"
            f" border: none; }}"
        )
        header.addWidget(self._status_pill)
        outer.addLayout(header)

        self._blurb = QLabel("")
        self._blurb.setWordWrap(True)
        self._blurb.setStyleSheet("color: palette(mid); border: none; padding: 0;")
        outer.addWidget(self._blurb)

        self._controls_row = QHBoxLayout()
        self._controls_row.setSpacing(8)
        outer.addLayout(self._controls_row)

        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(
            "color: palette(mid); border: none; padding: 0; font-size: 11px;"
        )
        outer.addWidget(self._detail)

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_blurb(self, text: str) -> None:
        self._blurb.setText(text)

    def set_status(self, ok: bool | None, label: str) -> None:
        color = _DOT_OK if ok else (_DOT_MISSING if ok is False else _DOT_MUTED)
        self._status_pill.setText(label)
        self._status_pill.setStyleSheet(
            f"QLabel {{ background: {color}; color: white;"
            f" border-radius: 8px; padding: 1px 8px; font-size: 11px;"
            f" border: none; }}"
        )

    def set_detail(self, text: str) -> None:
        self._detail.setText(text)

    def add_control(self, widget: QWidget) -> None:
        self._controls_row.addWidget(widget)

    def add_stretch(self) -> None:
        self._controls_row.addStretch(1)


class GamdlSetupScreen(QWidget):
    """Walkthrough that gates the lossless-upgrade flow."""

    back_requested = Signal()
    proceed_requested = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._cfg: Config | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 32, 48, 32)
        outer.setSpacing(16)

        title = QLabel("Lossless upgrade setup")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        outer.addWidget(title)

        intro = QLabel(
            "gamdl uses your Apple Music subscription to download ALAC "
            "originals. Two files are required, plus the gamdl tool itself."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: palette(mid);")
        outer.addWidget(intro)

        # Scrollable card stack so this still works on small windows.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        cards_container = QWidget()
        cards_layout = QVBoxLayout(cards_container)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(10)

        # Step 1 — cookies
        self._cookies_card = _StepCard()
        self._cookies_card.set_title("Step 1 — Apple Music cookies")
        self._cookies_card.set_blurb(
            "Install the Cookie-Editor browser extension, sign into "
            "music.apple.com, and export cookies in Netscape format."
        )
        self._cookies_btn = QPushButton("Choose cookies.txt…")
        self._cookies_btn.clicked.connect(self._on_pick_cookies)
        self._cookies_card.add_control(self._cookies_btn)
        self._cookies_path_label = QLabel("Not set")
        self._cookies_path_label.setStyleSheet(
            "color: palette(mid); border: none; padding: 0; font-size: 11px;"
        )
        self._cookies_card.add_control(self._cookies_path_label)
        self._cookies_card.add_stretch()
        cards_layout.addWidget(self._cookies_card)

        # Step 2 — wvd
        self._wvd_card = _StepCard()
        self._wvd_card.set_title("Step 2 — Widevine device file")
        self._wvd_card.set_blurb(
            "A .wvd file from a Widevine L3 CDM dump. We can't host or "
            "distribute these — see the gamdl README for sources."
        )
        self._wvd_btn = QPushButton("Choose device.wvd…")
        self._wvd_btn.clicked.connect(self._on_pick_wvd)
        self._wvd_card.add_control(self._wvd_btn)
        self._wvd_path_label = QLabel("Not set")
        self._wvd_path_label.setStyleSheet(
            "color: palette(mid); border: none; padding: 0; font-size: 11px;"
        )
        self._wvd_card.add_control(self._wvd_path_label)
        self._wvd_card.add_stretch()
        cards_layout.addWidget(self._wvd_card)

        # Step 3 — gamdl
        self._gamdl_card = _StepCard()
        self._gamdl_card.set_title("Step 3 — Install gamdl")
        self._gamdl_card.set_blurb(
            "gamdl must be installed and on your PATH. Use the button to "
            "install or reinstall it via pip (--user)."
        )
        self._gamdl_btn = QPushButton("Install / Reinstall")
        self._gamdl_btn.clicked.connect(self._on_install_gamdl)
        self._gamdl_card.add_control(self._gamdl_btn)
        self._gamdl_version_label = QLabel("Not found")
        self._gamdl_version_label.setStyleSheet(
            "color: palette(mid); border: none; padding: 0; font-size: 11px;"
        )
        self._gamdl_card.add_control(self._gamdl_version_label)
        self._gamdl_card.add_stretch()
        cards_layout.addWidget(self._gamdl_card)

        # Step 4 — roll-up
        self._summary_card = _StepCard()
        self._summary_card.set_title("Step 4 — Ready to upgrade?")
        self._summary_card.set_blurb("")
        cards_layout.addWidget(self._summary_card)

        cards_layout.addStretch(1)
        scroll.setWidget(cards_container)
        outer.addWidget(scroll, 1)

        # Footer actions
        actions = QHBoxLayout()
        self._back_btn = QPushButton("← Back")
        self._back_btn.clicked.connect(self.back_requested)
        actions.addWidget(self._back_btn)
        actions.addStretch(1)
        self._continue_btn = QPushButton("Continue to upgrade →")
        self._continue_btn.setStyleSheet("padding: 6px 16px; font-weight: 600;")
        self._continue_btn.clicked.connect(self.proceed_requested)
        actions.addWidget(self._continue_btn)
        outer.addLayout(actions)

    # ---- public API ----
    def show_for(self, cfg: Config) -> None:
        self._cfg = cfg
        self._refresh()

    # ---- step 1: cookies ----
    @Slot()
    def _on_pick_cookies(self) -> None:
        start_dir = ""
        if self._cfg and self._cfg.gamdl_cookies_path:
            start_dir = str(Path(self._cfg.gamdl_cookies_path).parent)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Apple Music cookies.txt",
            start_dir,
            "Cookie files (*.txt);;All files (*)",
        )
        if not path:
            return
        try:
            save_global_config({"gamdl": {"cookies_path": path}})
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Could not save",
                f"Failed to write global config: {exc}",
            )
            return
        if self._cfg is not None:
            self._cfg.gamdl_cookies_path = path
        self._refresh()

    # ---- step 2: wvd ----
    @Slot()
    def _on_pick_wvd(self) -> None:
        start_dir = ""
        if self._cfg and self._cfg.gamdl_wvd_path:
            start_dir = str(Path(self._cfg.gamdl_wvd_path).parent)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Widevine device.wvd",
            start_dir,
            "Widevine device files (*.wvd);;All files (*)",
        )
        if not path:
            return
        try:
            save_global_config({"gamdl": {"wvd_path": path}})
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Could not save",
                f"Failed to write global config: {exc}",
            )
            return
        if self._cfg is not None:
            self._cfg.gamdl_wvd_path = path
        self._refresh()

    # ---- step 3: gamdl install ----
    @Slot()
    def _on_install_gamdl(self) -> None:
        self._gamdl_btn.setEnabled(False)
        self._gamdl_btn.setText("Installing…")
        self._gamdl_version_label.setText("Running pip install --user gamdl…")
        # Keep this synchronous-but-brief: pip writes to a wheelhouse; spinning
        # a worker just to disable a button is overkill. The UI stays
        # responsive enough for this kind of one-shot install.
        try:
            proc = subprocess.run(
                ["pip", "install", "--user", "gamdl"],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            QMessageBox.warning(
                self,
                "Install failed",
                f"Could not run pip: {exc}",
            )
            self._gamdl_btn.setEnabled(True)
            self._gamdl_btn.setText("Install / Reinstall")
            self._refresh()
            return
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
            QMessageBox.warning(
                self,
                "Install failed",
                "pip install --user gamdl exited with code "
                f"{proc.returncode}.\n\n" + "\n".join(tail),
            )
        self._gamdl_btn.setEnabled(True)
        self._gamdl_btn.setText("Install / Reinstall")
        self._refresh()

    # ---- refresh + summary ----
    def _refresh(self) -> None:
        cookies = self._cfg.gamdl_cookies_path if self._cfg else ""
        wvd = self._cfg.gamdl_wvd_path if self._cfg else ""

        # Step 1
        cookies_ok = _path_is_readable(cookies)
        self._cookies_card.set_status(cookies_ok, "ready" if cookies_ok else "missing")
        if cookies:
            self._cookies_path_label.setText(cookies)
            self._cookies_path_label.setToolTip(cookies)
        else:
            self._cookies_path_label.setText("Not set")
            self._cookies_path_label.setToolTip("")

        # Step 2
        wvd_ok = _path_is_readable(wvd)
        self._wvd_card.set_status(wvd_ok, "ready" if wvd_ok else "missing")
        if wvd:
            self._wvd_path_label.setText(wvd)
            self._wvd_path_label.setToolTip(wvd)
        else:
            self._wvd_path_label.setText("Not set")
            self._wvd_path_label.setToolTip("")

        # Step 3
        version = _gamdl_version()
        gamdl_ok = version is not None
        self._gamdl_card.set_status(gamdl_ok, "ready" if gamdl_ok else "not found")
        self._gamdl_version_label.setText(version or "Not found")

        # Step 4 — roll-up
        all_ok = cookies_ok and wvd_ok and gamdl_ok
        if all_ok:
            self._summary_card.set_status(True, "all set")
            self._summary_card.set_blurb(
                "All three prerequisites are ready. Click Continue to start "
                "the lossless upgrade."
            )
            self._summary_card.set_detail("")
        else:
            missing: list[str] = []
            if not cookies_ok:
                missing.append("Apple Music cookies.txt")
            if not wvd_ok:
                missing.append("Widevine .wvd device file")
            if not gamdl_ok:
                missing.append("gamdl on PATH")
            self._summary_card.set_status(False, "blocked")
            self._summary_card.set_blurb(
                "Still missing: " + ", ".join(missing)
            )
            self._summary_card.set_detail(
                "Fix the steps above; this section updates as you do."
            )
        self._continue_btn.setEnabled(all_ok)
