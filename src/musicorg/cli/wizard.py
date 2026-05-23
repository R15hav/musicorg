"""Interactive end-to-end wizard.

The user picks a music folder, answers a few questions, and the wizard runs
every pipeline phase in order with confirmations between major stages. Power
users can still drive individual phases via the top-level commands; this is
the recommended entry point for everyone else.
"""

from __future__ import annotations

import csv
import datetime
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn

from .. import approval, backup, canonicalize, dedupe, executor, planner, refingerprint, resolve, scan, upgrade
from ..config import (
    Config,
    GLOBAL_CONFIG,
    ensure_state_dir,
    load_config,
    save_global_config,
    save_library_config,
    slugify_path,
)
from ..lookup.breaker import CircuitBreaker
from ..tags import AUDIO_EXTS
from ..tags import read as read_tags


console = Console()
err = Console(stderr=True)


def _banner(text: str) -> None:
    console.print(Panel.fit(text, border_style="cyan"))


def _ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _ensure_dep(module: str, install_name: str | None = None) -> bool:
    """Return True if the module is importable (after best-effort install)."""
    install_name = install_name or module
    try:
        importlib.import_module(module)
        return True
    except ImportError:
        pass
    console.print(f"[yellow]{module} is not installed.[/yellow]")
    if not Confirm.ask(f"Install {install_name} now?", default=True):
        return False
    cmd = [sys.executable, "-m", "pip", "install", "--user", "--break-system-packages", install_name]
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    rc = subprocess.call(cmd)
    if rc != 0:
        console.print(f"[red]install failed (rc={rc})[/red]")
        return False
    importlib.invalidate_caches()
    try:
        importlib.import_module(module)
        return True
    except ImportError as e:
        console.print(f"[red]still cannot import {module}: {e}[/red]")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# stage 1 — file tree
# ─────────────────────────────────────────────────────────────────────────────

def _stage_scan(cfg: Config) -> int:
    """Walk the library and write 01_tags.csv. Returns file count."""
    state = cfg.state_path
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(), TaskProgressColumn(), TimeElapsedColumn(),
        console=err,
    ) as bar:
        task = bar.add_task("scanning", total=None)
        def cb(i: int, total: int, _p: Path) -> None:
            bar.update(task, total=total, completed=i)
        tracks = scan.scan(cfg.root_path, cfg, progress=cb)
    stats = scan.write_tags_csv(tracks, state / "01_tags.csv")
    console.print(
        f"[green]scan[/green]: {stats.total} files "
        f"(mutagen={stats.mutagen_ok}, ffprobe={stats.ffprobe_ok}, "
        f"mediainfo={stats.mediainfo_ok}, unreadable={stats.none})"
    )
    return stats.total


def _stage_dedupe(cfg: Config) -> int:
    state = cfg.state_path
    rows = dedupe.read_tags_csv(state / "01_tags.csv")
    winners, losers, groups = dedupe.group_duplicates(rows)
    dedupe.write_dedupe_outputs(winners, losers, groups, state, source_rows=rows)
    console.print(f"[green]dedupe[/green]: {len(groups)} duplicate groups, {len(winners)} winners, {len(losers)} losers")
    return len(losers)


def _stage_resolve(cfg: Config) -> dict:
    state = cfg.state_path
    stats = resolve.resolve_winners(state / "07_winners.csv", state / "08_resolved.csv", default_country=cfg.default_country)
    console.print(f"[green]resolve[/green]: confidence={stats.get('confidence', {})}, country={stats.get('country', {})}")
    return stats


def _stage_plan(cfg: Config) -> dict:
    state = cfg.state_path
    stats = planner.plan(state / "08_resolved.csv", state / "09_plan.csv", cfg.root_path)
    console.print(f"[green]plan[/green]: {stats.get('total', 0)} files routed; by_country={stats.get('by_country', {})}")
    _show_plan_preview(state / "09_plan.csv")
    return stats


def _show_plan_preview(plan_csv: Path, max_rows: int = 10) -> None:
    t = Table(title="plan preview (first 10 rows)", show_lines=False, padding=(0, 1))
    t.add_column("country"); t.add_column("album"); t.add_column("year"); t.add_column("title"); t.add_column("→ destination", overflow="fold")
    with plan_csv.open() as f:
        for i, row in enumerate(csv.DictReader(f)):
            if i >= max_rows: break
            t.add_row(row.get("country", ""), row.get("album_or_movie", ""), row.get("year", ""),
                      row.get("title", ""), row.get("destination", ""))
    console.print(t)


def _stage_apply(cfg: Config, mode: str) -> dict:
    state = cfg.state_path
    result = executor.execute_plan(
        plan_csv_path=state / "09_plan.csv",
        dups_csv_path=state / "07_duplicates.csv",
        state_dir=state,
        mode=mode,  # type: ignore[arg-type]
        dest=None,
        dry_run=False,
    )
    console.print(
        f"[green]apply[/green]: moved={result.get('moved', 0)}, "
        f"dups_quarantined={result.get('dup_moved', 0)}, "
        f"collisions={result.get('collisions', 0)}, errors={result.get('errors', 0)}"
    )
    if result.get("undo_script"):
        console.print(f"[dim]undo:[/dim] {result['undo_script']}")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# stage 2 — canonical metadata
# ─────────────────────────────────────────────────────────────────────────────

def _stage_canonicalize(cfg: Config, tiers: list[str]) -> dict:
    state = cfg.state_path
    music_root = cfg.root_path / "Music"
    if not music_root.exists():
        console.print(f"[red]missing {music_root} — run organize stage 1 first[/red]")
        return {}

    from ..lookup import chain  # local import — heavy network deps inside

    files: list[Path] = []
    for p in music_root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in AUDIO_EXTS: continue
        if any(part in {"_duplicates", "_misc", "_replaced", "_upgrade_staging"} for part in p.parts):
            continue
        files.append(p)
    files.sort()

    merged_path = state / "16_merged.csv"
    fieldnames = [
        "source_path", "decision", "confidence",
        "cur_title", "cur_artist", "cur_album", "cur_year", "cur_track", "cur_duration", "query",
        "api_title", "api_artist", "api_album", "api_year", "api_track_num", "api_genre",
        "api_duration", "api_track_view_url",
        "title_score", "artist_score", "duration_score",
        "album_bonus", "year_penalty", "version_penalty", "sparse_cap",
        "apple_music_url", "adam_id", "storefront", "isrc",
        "jio_title", "jio_artist", "jio_album", "jio_year", "jio_language", "jio_perma_url",
        "shazam_title", "shazam_artist", "shazam_album", "shazam_year",
        "shazam_label", "shazam_isrc", "shazam_key", "shazam_url", "shazam_genre", "shazam_image",
    ]
    stats = {"auto_apply": 0, "review": 0, "low": 0, "no_match": 0}

    with merged_path.open("w", newline="", encoding="utf-8") as f, Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(), TaskProgressColumn(), TimeElapsedColumn(),
        console=err,
    ) as bar:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        task = bar.add_task(f"lookup ({','.join(tiers)})", total=len(files))
        for p in files:
            track = read_tags(p)
            best, decision = chain(track, cfg)
            stats[decision] = stats.get(decision, 0) + 1
            row: dict[str, object] = {
                "source_path": str(p), "decision": decision,
                "confidence": getattr(best, "confidence", 0.0) if best else 0.0,
                "cur_title": track.title, "cur_artist": track.artist,
                "cur_album": track.album, "cur_year": track.year, "cur_track": track.track,
                "cur_duration": track.duration_sec,
            }
            if best:
                row.update({
                    "api_title": best.title, "api_artist": best.artist, "api_album": best.album,
                    "api_year": best.year, "api_track_num": best.track_num, "api_genre": best.genre,
                    "api_track_view_url": best.apple_music_url,
                    "apple_music_url": best.apple_music_url, "adam_id": best.adam_id,
                    "storefront": best.storefront, "isrc": best.isrc,
                })
                for k, v in (best.score_parts or {}).items():
                    row[k] = v
            w.writerow(row)
            bar.update(task, advance=1)

    t = Table(title="canonicalize buckets")
    t.add_column("bucket"); t.add_column("count", justify="right")
    for k in ("auto_apply", "review", "low", "no_match"):
        t.add_row(k, str(stats.get(k, 0)))
    console.print(t)
    return stats


def _stage_canonical_apply(cfg: Config) -> None:
    state = cfg.state_path
    ts = _ts()
    snap = state / "backups" / f"tag_snapshot_{ts}.json"
    undo = state / f"undo_phase18_{ts}.py"
    diff_csv = state / "17_dryrun_diff.csv"
    merged = state / "16_merged.csv"
    canonicalize.build_diff(merged, diff_csv)
    result = canonicalize.apply(
        diff_csv=diff_csv, merged_csv=merged,
        snapshot_path=snap, undo_script_path=undo,
        dry_run=False, rename=True, include_low=False,
    )
    console.print(
        f"[green]canonical-apply[/green] (auto): touched={result.files_touched}, "
        f"renamed={result.renamed}, guarded={result.guarded}, collisions={result.collisions}, errors={result.errors}"
    )
    console.print(f"[dim]undo:[/dim] {result.undo_script_path}")

    approvals = state / "19_approvals.json"
    if approvals.exists():
        snap2 = state / "backups" / f"tag_snapshot_phase20_{ts}.json"
        undo2 = state / f"undo_phase20_{ts}.py"
        result2 = canonicalize.apply_approvals(
            merged_csv=merged, approvals_json=approvals,
            snapshot_path=snap2, undo_script_path=undo2,
            dry_run=False, rename=True,
        )
        console.print(
            f"[green]canonical-apply[/green] (approvals): touched={result2.files_touched}, "
            f"renamed={result2.renamed}, errors={result2.errors}"
        )
        console.print(f"[dim]undo:[/dim] {result2.undo_script_path}")


def _resolve_editor() -> str | None:
    for env in ("VISUAL", "EDITOR"):
        v = os.environ.get(env)
        if v and shutil.which(v.split()[0]):
            return v
    for fallback in ("nano", "vim", "vi"):
        if shutil.which(fallback):
            return fallback
    return None


def _approve_via_editor(cfg: Config) -> bool:
    state = cfg.state_path
    editor = _resolve_editor()
    if not editor:
        console.print("[red]no editor found ($VISUAL/$EDITOR/nano/vim missing)[/red]")
        return False
    review_csv = state / "19_review.csv"
    n = approval.build_review_csv(state / "16_merged.csv", review_csv)
    console.print(f"opening {review_csv} in {editor} ({n} rows to approve)")
    console.print("[dim]edit the 'approve' column: itunes | jiosaavn | shazam | manual | skip[/dim]")
    Prompt.ask("[bold]Press enter to launch the editor[/bold]", default="")
    rc = subprocess.call([editor, str(review_csv)])
    if rc != 0:
        console.print(f"[yellow]editor exited with {rc}[/yellow]")
        return False
    try:
        n_ok, _ = approval.parse_review_csv(review_csv, state / "19_approvals.json")
    except approval.ApprovalValidationError as e:
        console.print(f"[red]validation failed:[/red] {e}")
        for row, field, issue in getattr(e, "errors", []):
            console.print(f"  row {row} field {field}: {issue}")
        return False
    console.print(f"[green]approvals recorded[/green]: {n_ok}")
    return True


def _approve_via_rule(cfg: Config, rule: str) -> bool:
    state = cfg.state_path
    n = approval.batch_rule(state / "16_merged.csv", state / "19_approvals.json", rule)
    console.print(f"[green]batch rule applied[/green]: {n} approvals (rule='{rule}')")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# stage 3 — upgrade
# ─────────────────────────────────────────────────────────────────────────────

def _stage_upgrade(cfg: Config, cookies: Path | None, wvd: Path | None) -> None:
    state = cfg.state_path
    candidates: list[dict] = []
    seen: set[str] = set()
    for csv_path in (state / "30_shazam_refingerprint.csv", state / "16_merged.csv"):
        if not csv_path.exists(): continue
        with csv_path.open() as f:
            for r in csv.DictReader(f):
                p = r.get("source_path") or r.get("path") or ""
                url = (r.get("apple_music_url") or r.get("api_track_view_url") or "").strip()
                if not p or not url or p in seen: continue
                seen.add(p)
                candidates.append({
                    "path": p, "apple_music_url": url,
                    "adam_id": r.get("adam_id", ""), "storefront": r.get("storefront", ""),
                })
    console.print(f"[bold]upgrade candidates:[/bold] {len(candidates)}")
    if not candidates:
        return
    if not Confirm.ask("Run gamdl upgrade now?", default=True):
        return
    result = upgrade.upgrade_batch(
        candidates=candidates,
        music_root=cfg.root_path,
        state_dir=state,
        dry_run=False,
        cookies_path=cookies,
        wvd_path=wvd,
    )
    t = Table(title="upgrade results")
    t.add_column("metric"); t.add_column("value", justify="right")
    for k, v in result.items():
        t.add_row(str(k), str(v))
    console.print(t)

    report = upgrade.permanent_skip_report(state)
    if report:
        t2 = Table(title="permanent skips")
        t2.add_column("reason"); t2.add_column("count", justify="right")
        for reason, items in report.items():
            t2.add_row(reason, str(len(items)))
        console.print(t2)


def _stage_refingerprint(cfg: Config) -> None:
    state = cfg.state_path
    breaker = CircuitBreaker(name="SHAZAMIO", threshold=cfg.shazam_breaker_threshold, marker_dir=state)
    if breaker.is_tripped():
        console.print(f"[yellow]Shazam circuit breaker tripped ({breaker.is_tripped().name}); skipping refingerprint[/yellow]")
        return
    console.print("[bold]running Shazam audio-fingerprint pass[/bold] (this takes a while — 1.5s per file)")
    result = refingerprint.refingerprint_lossy(
        music_root=cfg.root_path / "Music",
        state_dir=state,
        lossy_only=True,
        breaker=breaker,
        sleep_sec=cfg.shazam_inter_request_sleep_sec,
    )
    console.print(f"[green]refingerprint[/green]: {result}")


# ─────────────────────────────────────────────────────────────────────────────
# main wizard
# ─────────────────────────────────────────────────────────────────────────────

def run_wizard() -> None:
    _banner("[bold cyan]musicorg — guided music library organizer[/bold cyan]\n"
            "Walks your library through: scan → dedupe → organize → canonical metadata → optional ALAC upgrade.\n"
            "Every step generates an undo. You can interrupt with Ctrl+C and resume.")

    # ── library + preferences ───────────────────────────────────────────────
    default_root = str(Path.cwd())
    root = Path(Prompt.ask("[bold]Music folder to organize[/bold]", default=default_root)).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        console.print(f"[red]not a directory: {root}[/red]"); raise SystemExit(2)

    default_slug = slugify_path(root)
    library = Prompt.ask("[bold]Library name[/bold] (used for state dir)", default=default_slug.split("-", 1)[0] if "-" in default_slug else default_slug)

    country = Prompt.ask(
        "[bold]Default country for unknown tracks[/bold]",
        choices=["bollywood", "hollywood", "unknown"], default="bollywood",
    )
    apply_mode = Prompt.ask(
        "[bold]How should files be placed?[/bold]",
        choices=["move", "copy", "symlink"], default="move",
    )

    cfg = load_config(library, root)
    cfg.library_root = str(root)
    cfg.default_country = country  # type: ignore[assignment]
    cfg.apply_mode = apply_mode  # type: ignore[assignment]
    ensure_state_dir(cfg)
    save_library_config(cfg, {
        "library": {"root": str(root)},
        "defaults": {"country": country, "apply_mode": apply_mode},
    })

    _banner(f"[bold]library:[/bold] {library}\n[bold]root:[/bold] {root}\n[bold]state:[/bold] {cfg.state_path}")

    # ── stage 1: file tree ──────────────────────────────────────────────────
    if Confirm.ask("\n[bold cyan]Stage 1[/bold cyan] — Scan & organize file tree?", default=True):
        n = _stage_scan(cfg)
        if n == 0:
            console.print("[red]no audio files found — nothing to do[/red]"); return
        _stage_dedupe(cfg)
        _stage_resolve(cfg)
        _stage_plan(cfg)
        if Confirm.ask(f"[bold]Apply this plan ({apply_mode})?[/bold]", default=True):
            _stage_apply(cfg, apply_mode)
        else:
            console.print("[yellow]skipped apply — the plan stays at 09_plan.csv for later[/yellow]")

    # ── stage 2: canonical metadata ─────────────────────────────────────────
    if Confirm.ask("\n[bold cyan]Stage 2[/bold cyan] — Clean up metadata via iTunes/JioSaavn/Shazam?", default=True):
        tiers = ["itunes", "jiosaavn"]
        if Confirm.ask("Include Shazam audio fingerprinting? (slower; needs `shazamio`)", default=True):
            if _ensure_dep("shazamio"):
                tiers.append("shazam")
            else:
                console.print("[yellow]proceeding without Shazam[/yellow]")
        stats = _stage_canonicalize(cfg, tiers)

        unresolved = stats.get("review", 0) + stats.get("low", 0)
        if unresolved:
            console.print(f"[bold]{unresolved} files need approval[/bold] (review + low buckets)")
            choice = Prompt.ask(
                "How to handle them?",
                choices=["batch", "edit", "skip"], default="batch",
            )
            if choice == "batch":
                rule = Prompt.ask("Priority rule", default="jiosaavn>shazam>itunes" if "shazam" in tiers else "jiosaavn>itunes")
                _approve_via_rule(cfg, rule)
            elif choice == "edit":
                _approve_via_editor(cfg)
            # skip → only auto_apply rows get applied below

        if Confirm.ask("[bold]Write tags + rename files now?[/bold]", default=True):
            _stage_canonical_apply(cfg)

    # ── stage 3: lossless upgrade ───────────────────────────────────────────
    if Confirm.ask("\n[bold cyan]Stage 3[/bold cyan] — Upgrade lossy tracks to ALAC via gamdl?", default=False):
        console.print("[dim]gamdl needs an Apple Music subscription, browser cookies, and a Widevine device file.[/dim]")
        if not _ensure_dep("shazamio"):
            console.print("[yellow]Shazam needed for the refingerprint pass — skipping upgrade[/yellow]")
        else:
            if shutil.which("gamdl") is None:
                console.print("[yellow]gamdl not on PATH[/yellow]")
                if Confirm.ask("Install gamdl now?", default=True):
                    subprocess.call([sys.executable, "-m", "pip", "install", "--user", "--break-system-packages", "gamdl"])

            cookies_in = Prompt.ask("Path to cookies.txt (blank to skip)", default="")
            wvd_in = Prompt.ask("Path to your .wvd device file (blank to skip)", default="")
            cookies = Path(cookies_in).expanduser() if cookies_in else None
            wvd = Path(wvd_in).expanduser() if wvd_in else None

            if Confirm.ask("Run Shazam refingerprint pass first? (harvests Apple Music URLs)", default=True):
                _stage_refingerprint(cfg)

            _stage_upgrade(cfg, cookies, wvd)

    # ── summary ─────────────────────────────────────────────────────────────
    _banner(
        "[bold green]done.[/bold green]\n"
        f"State + logs:   {cfg.state_path}\n"
        f"Undo scripts:   {cfg.state_path}/undo_*.sh, undo_phase*.py, undo_upgrade*.py\n"
        f"Snapshots:      {cfg.state_path}/backups/\n\n"
        f"To roll back:\n"
        f"  musicorg --library {library} undo                    # file moves\n"
        f"  musicorg --library {library} canonical-undo --latest # tag writes\n"
    )
