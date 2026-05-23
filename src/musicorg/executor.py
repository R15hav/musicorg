"""Apply ``09_plan.csv`` to the filesystem and quarantine duplicates.

Supports move / copy / symlink modes; collisions get ``" (2)"``, ``" (3)"``
suffixes and are logged to ``11_collisions.csv``. An ``undo_<TS>.sh`` script
is generated that reverses every successful action in reverse order.
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Literal


ApplyMode = Literal["move", "copy", "symlink"]


def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _dedupe_path(dst: Path) -> Path:
    if not dst.exists():
        return dst
    base = dst.with_suffix("")
    ext = dst.suffix
    i = 2
    while True:
        cand = Path(f"{base} ({i}){ext}")
        if not cand.exists():
            return cand
        i += 1


def _apply_one(src: Path, dst: Path, mode: ApplyMode) -> None:
    if mode == "move":
        shutil.move(str(src), str(dst))
    elif mode == "copy":
        shutil.copy2(str(src), str(dst))
    elif mode == "symlink":
        os.symlink(str(src), str(dst))
    else:
        raise ValueError(f"unknown apply mode: {mode}")


def _undo_for(mode: ApplyMode, src: Path, dst: Path) -> str:
    if mode == "move":
        return f"mv -n {_shell_quote(str(dst))} {_shell_quote(str(src))}"
    if mode == "copy":
        return f"rm -f {_shell_quote(str(dst))}"
    if mode == "symlink":
        return f"rm -f {_shell_quote(str(dst))}"
    return ""


def _write_undo(undo_path: Path, lines: list[str]) -> None:
    header = [
        "#!/bin/bash",
        "# Auto-generated undo script.",
        "# Reverses every successful action in reverse order.",
        "set -e",
        "",
    ]
    body = list(reversed(lines))
    undo_path.write_text("\n".join(header + body) + "\n")
    undo_path.chmod(0o755)


def _resolve_dest(plan_dst: str, mode: ApplyMode, dest_override: Path | None) -> Path:
    """For ``copy`` with ``dest`` override, rebase the plan destination."""
    dst = Path(plan_dst)
    if mode == "copy" and dest_override is not None:
        override = Path(dest_override)
        # The plan destination is absolute under ``<library>/Music/...``.
        # Rebase by stripping a leading ``/.../Music/`` so the override
        # receives ``Music/<bucket>/...``.
        parts = dst.parts
        if "Music" in parts:
            idx = parts.index("Music")
            rel = Path(*parts[idx:])
            return override / rel
        return override / dst.name
    return dst


def execute_plan(
    plan_csv_path: Path,
    dups_csv_path: Path,
    state_dir: Path,
    mode: ApplyMode,
    dest: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Apply every row in ``09_plan.csv`` then quarantine duplicates.

    Returns a stats dict: ``{moved, noop, missing, dup_moved, collisions,
    errors, undo_script, collisions_csv, log_path}``. In ``dry_run`` mode
    no filesystem changes happen but the same paths are computed and the
    log is written.
    """
    plan_csv_path = Path(plan_csv_path)
    dups_csv_path = Path(dups_csv_path)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    log_dir = state_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"11_execute_{ts}.log"
    undo_path = state_dir / f"undo_{ts}.sh"
    collisions_csv = state_dir / "11_collisions.csv"

    plan_rows: list[dict] = []
    if plan_csv_path.exists():
        with plan_csv_path.open("r", encoding="utf-8", newline="") as f:
            plan_rows = list(csv.DictReader(f))
    dup_rows: list[dict] = []
    if dups_csv_path.exists():
        with dups_csv_path.open("r", encoding="utf-8", newline="") as f:
            dup_rows = list(csv.DictReader(f))

    log_lines: list[str] = []

    def log(msg: str) -> None:
        log_lines.append(msg)

    log(f"Plan rows: {len(plan_rows)}  Duplicates: {len(dup_rows)}  mode={mode}  dry_run={dry_run}")

    moved = 0
    noop = 0
    missing = 0
    errors = 0
    collisions: list[dict] = []
    undo_lines: list[str] = []

    log("\n=== WINNERS ===")
    for r in plan_rows:
        src_str = r.get("source", "")
        plan_dst_str = r.get("destination", "")
        src = Path(src_str)
        dst = _resolve_dest(plan_dst_str, mode, dest)

        if not src.exists():
            log(f"MISSING SRC: {src_str}")
            missing += 1
            continue
        if str(src) == str(dst):
            noop += 1
            continue

        try:
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                new_dst = _dedupe_path(dst)
                log(f"COLLISION: {dst} exists, using {new_dst}")
                collisions.append({**r, "actual_destination": str(new_dst)})
                dst = new_dst
            if dry_run:
                log(f"DRY-RUN {mode}: {src} -> {dst}")
            else:
                _apply_one(src, dst, mode)
                undo_lines.append(_undo_for(mode, src, dst))
            moved += 1
        except Exception as e:
            log(f"ERROR: {src} -> {dst}: {e}")
            errors += 1

    log("\n=== DUPLICATES (quarantine) ===")
    dup_moved = 0
    # Quarantine root: prefer the dest override (copy mode) else derive
    # from each duplicate's path by treating its first ``Music`` ancestor
    # as the library root.
    for r in dup_rows:
        src_str = r.get("path", "")
        src = Path(src_str)
        if not src.exists():
            log(f"MISSING DUP SRC: {src_str}")
            missing += 1
            continue

        if dest is not None and mode == "copy":
            dup_root = Path(dest) / "Music" / "_duplicates"
            try:
                rel = src.relative_to(Path(dest))
            except ValueError:
                rel = Path(src.name)
        else:
            parts = src.parts
            if "Music" in parts:
                idx = parts.index("Music")
                lib_root = Path(*parts[: idx + 1]).parent
                dup_root = lib_root / "Music" / "_duplicates"
                try:
                    rel = src.relative_to(lib_root)
                except ValueError:
                    rel = Path(src.name)
            else:
                dup_root = src.parent / "_duplicates"
                rel = Path(src.name)

        dst = dup_root / rel
        try:
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst = _dedupe_path(dst)
            if dry_run:
                log(f"DRY-RUN DUP {mode}: {src} -> {dst}")
            else:
                _apply_one(src, dst, mode)
                undo_lines.append(_undo_for(mode, src, dst))
            dup_moved += 1
            log(f"DUP: {src} -> {dst}")
        except Exception as e:
            log(f"ERROR DUP: {src} -> {dst}: {e}")
            errors += 1

    if not dry_run and undo_lines:
        _write_undo(undo_path, undo_lines)

    if collisions:
        with collisions_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(collisions[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(collisions)

    log("")
    log("=== Summary ===")
    log(f"Winners {'(planned)' if dry_run else 'moved'}: {moved}")
    log(f"Winners no-op:    {noop}")
    log(f"Duplicates {'(planned)' if dry_run else 'moved'}: {dup_moved}")
    log(f"Missing sources:  {missing}")
    log(f"Collisions:       {len(collisions)}")
    log(f"Errors:           {errors}")
    if not dry_run and undo_lines:
        log(f"Undo script:      {undo_path}")

    log_path.write_text("\n".join(log_lines) + "\n")

    return {
        "moved": moved,
        "noop": noop,
        "missing": missing,
        "dup_moved": dup_moved,
        "collisions": len(collisions),
        "errors": errors,
        "undo_script": undo_path if (not dry_run and undo_lines) else None,
        "collisions_csv": collisions_csv if collisions else None,
        "log_path": log_path,
        "dry_run": dry_run,
        "mode": mode,
    }


def run_undo(undo_script_path: Path) -> int:
    """Execute a previously generated ``undo_<TS>.sh``. Returns the exit code."""
    p = Path(undo_script_path)
    if not p.exists():
        raise FileNotFoundError(p)
    if not os.access(p, os.X_OK):
        p.chmod(0o755)
    result = subprocess.run(["/bin/bash", str(p)], check=False)
    return result.returncode
