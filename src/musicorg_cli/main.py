"""Typer CLI entry point for musicorg.

Every command resolves a Config via `--library` / CWD, dispatches to a single
core function, and renders results via Rich. The CLI layer owns user-facing
output; the library layer returns data structures.
"""

from __future__ import annotations

import csv
import datetime
import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, BarColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn

from musicorg import (
    backup, canonicalize, dedupe, executor, misc, planner, refingerprint,
    resolve, upgrade, zip_probe, approval,
)
from musicorg.scan import scan, write_tags_csv
from musicorg.config import Config, GLOBAL_CONFIG, STATE_ROOT, ensure_state_dir, load_config, save_global_config, save_library_config
from musicorg.lookup.breaker import CircuitBreaker
from musicorg.models import ProgressEvent


app = typer.Typer(
    name="musicorg",
    help="Linux terminal music organizer — scan, dedupe, canonicalize, upgrade.",
    no_args_is_help=False,
    add_completion=True,
    invoke_without_command=True,
)
config_app = typer.Typer(help="Read/write config.")
app.add_typer(config_app, name="config")

console = Console()
err = Console(stderr=True)


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

class _G:
    """Module-level globals set by the main callback (avoids passing ctx around)."""
    library: Optional[str] = None
    state_dir: Optional[Path] = None
    config_path: Optional[Path] = None
    quiet: bool = False
    verbose: bool = False
    json_out: bool = False


@app.callback()
def _root(
    ctx: typer.Context,
    library: Optional[str] = typer.Option(None, "--library", help="Library slug or name."),
    state_dir: Optional[Path] = typer.Option(None, "--state-dir", help="Override state directory."),
    config_path: Optional[Path] = typer.Option(None, "--config", help="Override config file."),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress info output."),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose output."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON progress for piping."),
) -> None:
    _G.library = library
    _G.state_dir = state_dir
    _G.config_path = config_path
    _G.quiet = quiet
    _G.verbose = verbose
    _G.json_out = json_out
    # If invoked with no subcommand, drop the user into the guided wizard.
    if ctx.invoked_subcommand is None:
        from .wizard import run_wizard
        run_wizard()
        raise typer.Exit(0)


@app.command("organize")
def organize_cmd() -> None:
    """Guided end-to-end wizard: scan → dedupe → organize → canonicalize → (optional) ALAC upgrade."""
    from .wizard import run_wizard
    run_wizard()


def _cfg(root: Optional[Path] = None) -> Config:
    cfg = load_config(_G.library, root)
    if _G.state_dir:
        cfg.state_dir = str(_G.state_dir)
    ensure_state_dir(cfg)
    return cfg


def _info(msg: str) -> None:
    if not _G.quiet:
        err.print(msg)


def _ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _emit(payload: dict) -> None:
    if _G.json_out:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()
    elif not _G.quiet:
        for k, v in payload.items():
            err.print(f"  {k:<22} {v}")


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — file tree
# ─────────────────────────────────────────────────────────────────────────────

@app.command("scan")
def scan_cmd(
    path: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True, resolve_path=True),
    exclude: list[Path] = typer.Option([], "--exclude", help="Subtree to skip (repeatable)."),
) -> None:
    """Walk PATH for audio files; write 01_tags.csv."""
    cfg = _cfg(path)
    cfg.library_root = str(path)
    save_library_config(cfg, {"library": {"root": str(path)}})
    state = cfg.state_path
    out = state / "01_tags.csv"

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=err,
        disable=_G.quiet,
    ) as bar:
        task = bar.add_task("scanning", total=None)
        def on_progress(ev: ProgressEvent) -> None:
            bar.update(task, total=ev.total, completed=ev.current)
        tracks = scan(cfg, root=path, extra_excludes=exclude, progress=on_progress)
    stats = write_tags_csv(tracks, out)
    if not tracks:
        err.print("[red]no audio files found[/red]")
        raise typer.Exit(2)
    err_rate = stats.none / max(1, stats.total)
    _info(f"[green]wrote[/green] {out}")
    _emit({
        "total": stats.total,
        "mutagen-ok": stats.mutagen_ok,
        "ffprobe-ok": stats.ffprobe_ok,
        "mediainfo-ok": stats.mediainfo_ok,
        "none": stats.none,
    })
    if err_rate > 0.05:
        err.print(f"[yellow]warning: {err_rate*100:.1f}% files unreadable[/yellow]")
        raise typer.Exit(3)


@app.command("dedupe")
def dedupe_cmd(
    threshold: float = typer.Option(0.95, "--threshold"),
    interactive: bool = typer.Option(False, "--interactive", help="Launch the two-pane dup-review TUI."),
) -> None:
    """Group duplicates and pick winners. Writes 07_winners.csv, 07_duplicates.csv, 07_groups.csv."""
    cfg = _cfg()
    state = cfg.state_path
    if interactive:
        from .tui.dup_review_app import DupReviewApp
        DupReviewApp(state).run()
        return
    rows = dedupe.read_tags_csv(state / "01_tags.csv")
    winners, losers, groups = dedupe.group_duplicates(rows)
    paths = dedupe.write_dedupe_outputs(winners, losers, groups, state, source_rows=rows)
    _info(f"{len(groups)} duplicate groups • {len(winners)} winners • {len(losers)} losers")
    for k, v in paths.items():
        _info(f"  -> {v}")


@app.command("fill")
def fill_cmd() -> None:
    """Launch the per-row card TUI to resolve files with non-empty needs_lookup."""
    from .tui.fill_app import FillUnknownsApp
    cfg = _cfg()
    resolved = cfg.state_path / "08_resolved.csv"
    if not resolved.exists():
        err.print(f"[red]missing {resolved} — run resolve first[/red]"); raise typer.Exit(2)
    FillUnknownsApp(resolved).run()


@app.command("resolve")
def resolve_cmd() -> None:
    """Apply junk-stripping + reconcile folder/tag/filename. Writes 08_resolved.csv."""
    cfg = _cfg()
    state = cfg.state_path
    out = state / "08_resolved.csv"
    stats = resolve.resolve_winners(state / "07_winners.csv", out, default_country=cfg.default_country)
    _info(f"[green]wrote[/green] {out}")
    _emit(stats)


@app.command(name="plan")
def plan_cmd(
    overrides: Optional[Path] = typer.Option(None, "--overrides", help="manual_fills.json"),
) -> None:
    """Build the destination tree plan. Writes 09_plan.csv."""
    cfg = _cfg()
    state = cfg.state_path
    out = state / "09_plan.csv"
    stats = planner.plan(state / "08_resolved.csv", out, cfg.root_path)
    _info(f"[green]wrote[/green] {out}")
    _emit(stats)


@app.command()
def apply(
    mode: str = typer.Option("move", "--mode", help="move|copy|symlink"),
    dest: Optional[Path] = typer.Option(None, "--dest"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Execute 09_plan.csv. Generates undo_<TS>.sh."""
    if mode not in {"move", "copy", "symlink"}:
        err.print(f"[red]bad --mode: {mode}[/red]"); raise typer.Exit(2)
    if mode == "copy" and dest is None:
        err.print("[red]--mode copy requires --dest[/red]"); raise typer.Exit(2)
    cfg = _cfg()
    state = cfg.state_path
    result = executor.execute_plan(
        plan_csv_path=state / "09_plan.csv",
        dups_csv_path=state / "07_duplicates.csv",
        state_dir=state,
        mode=mode,  # type: ignore[arg-type]
        dest=dest,
        dry_run=dry_run,
    )
    _emit(result)
    if result.get("missing", 0):
        raise typer.Exit(4)


@app.command()
def quarantine() -> None:
    """Move dup losers to <music_root>/Music/_duplicates/<subpath>."""
    cfg = _cfg()
    state = cfg.state_path
    plan_csv = state / "09_plan.csv"
    if not plan_csv.exists():
        # Synthesize an empty plan so execute_plan only quarantines.
        plan_csv.write_text("source,destination,action\n")
    result = executor.execute_plan(
        plan_csv_path=plan_csv,
        dups_csv_path=state / "07_duplicates.csv",
        state_dir=state,
        mode="move",
        dest=None,
        dry_run=False,
    )
    _emit(result)


@app.command(name="misc-sweep")
def misc_sweep_cmd(
    include_images: bool = typer.Option(False, "--include-images"),
    include_text: bool = typer.Option(False, "--include-text"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Sweep non-audio top-level files to <music_root>/Music/_misc/."""
    cfg = _cfg()
    result = misc.sweep_misc(cfg.root_path, cfg.root_path, include_images=include_images, include_text=include_text, dry_run=dry_run)
    _emit(result)


@app.command(name="zip-probe")
def zip_probe_cmd(
    quarantine_duplicates: bool = typer.Option(False, "--quarantine-duplicates"),
) -> None:
    """For every .zip in the music tree, diff contents against organized filenames."""
    cfg = _cfg()
    organized_filenames: set[str] = set()
    music_dir = cfg.root_path / "Music"
    if music_dir.exists():
        organized_filenames = {p.name for p in music_dir.rglob("*") if p.is_file()}
    reports = zip_probe.probe_zips(cfg.root_path, cfg.root_path, organized_filenames, quarantine_duplicates=quarantine_duplicates)
    t = Table(title=f"zip-probe ({len(reports)} archives)")
    t.add_column("zip"); t.add_column("entries"); t.add_column("match%"); t.add_column("status")
    for r in reports:
        t.add_row(str(r.get("zip", "")), str(r.get("entries", "")), f"{r.get('match_pct', 0):.0f}", r.get("status", ""))
    console.print(t)


@app.command()
def undo(
    list_: bool = typer.Option(False, "--list"),
    script: Optional[Path] = typer.Option(None, "--script"),
) -> None:
    """Run a file-move undo script."""
    cfg = _cfg()
    state = cfg.state_path
    candidates = sorted(state.glob("undo_*.sh"), key=lambda p: p.stat().st_mtime, reverse=True)
    if list_:
        for p in candidates:
            console.print(p)
        return
    target = script or (candidates[0] if candidates else None)
    if target is None:
        err.print("[red]no undo script found[/red]"); raise typer.Exit(1)
    rc = executor.run_undo(target)
    raise typer.Exit(rc)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — canonical metadata
# ─────────────────────────────────────────────────────────────────────────────

@app.command("canonicalize")
def canonicalize_cmd(
    tiers: str = typer.Option("itunes,jiosaavn,shazam", "--tiers"),
    skip_tier: Optional[str] = typer.Option(None, "--skip-tier"),
    only_decisions: Optional[str] = typer.Option(None, "--only-decisions"),
) -> None:
    """Run tiered API lookups. Writes 13_itunes.csv, 15_jiosaavn.csv, 16_shazam.csv, 16_merged.csv."""
    cfg = _cfg()
    state = cfg.state_path

    requested = [t.strip() for t in tiers.split(",") if t.strip()]
    if skip_tier:
        requested = [t for t in requested if t != skip_tier]

    breaker = CircuitBreaker(name="SHAZAMIO", threshold=cfg.shazam_breaker_threshold, marker_dir=state)
    if "shazam" in requested and breaker.is_tripped():
        err.print("[yellow]Shazam circuit breaker is tripped — skipping that tier.[/yellow]")
        requested = [t for t in requested if t != "shazam"]

    # Drive via the in-package chain(): walk every audio file under cfg.root_path/Music.
    music_root = cfg.root_path / "Music"
    if not music_root.exists():
        err.print(f"[red]no organized music tree at {music_root} (run apply first)[/red]")
        raise typer.Exit(2)

    from musicorg.tags import AUDIO_EXTS, read as read_tags
    from musicorg.lookup import chain

    files: list[Path] = []
    for p in music_root.rglob("*"):
        if not p.is_file(): continue
        if p.suffix.lower() not in AUDIO_EXTS: continue
        if any(part in {"_duplicates", "_misc", "_replaced", "_upgrade_staging"} for part in p.parts):
            continue
        files.append(p)
    files.sort()
    _info(f"canonicalize: {len(files)} files, tiers={requested}")

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
        console=err, disable=_G.quiet,
    ) as bar:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        task = bar.add_task("lookup", total=len(files))
        for i, p in enumerate(files, 1):
            track = read_tags(p)
            best, decision = chain(track, cfg)
            parts = best.score_parts if best else {}
            stats[decision] = stats.get(decision, 0) + 1
            row = {"source_path": str(p), "decision": decision,
                   "confidence": getattr(best, "confidence", 0.0) if best else 0.0,
                   "cur_title": track.title, "cur_artist": track.artist,
                   "cur_album": track.album, "cur_year": track.year, "cur_track": track.track,
                   "cur_duration": track.duration_sec}
            if best:
                row.update({
                    "api_title": best.title, "api_artist": best.artist, "api_album": best.album,
                    "api_year": best.year, "api_track_num": best.track_num, "api_genre": best.genre,
                    "api_track_view_url": best.apple_music_url,
                    "apple_music_url": best.apple_music_url, "adam_id": best.adam_id,
                    "storefront": best.storefront, "isrc": best.isrc,
                })
                for k, v in (parts or {}).items():
                    row[k] = v
            w.writerow(row)
            bar.update(task, advance=1)

    _info(f"[green]wrote[/green] {merged_path}")
    _emit(stats)


@app.command()
def review(
    export: bool = typer.Option(False, "--export"),
    out: Optional[Path] = typer.Option(None, "--out"),
    import_: Optional[Path] = typer.Option(None, "--import", help="Path to user-edited review CSV."),
    interactive: bool = typer.Option(False, "--interactive"),
) -> None:
    """Round-trip a CSV through the user for manual approvals."""
    cfg = _cfg()
    state = cfg.state_path
    merged = state / "16_merged.csv"

    if interactive:
        # Plan-editor mode when only a 09_plan.csv exists; canonical mode when
        # a merged.csv is present (the headline approval flow).
        if merged.exists():
            from .tui.canonical_app import CanonicalReviewApp
            CanonicalReviewApp(state, merged).run()
        elif (state / "09_plan.csv").exists():
            from .tui.review_app import PlanReviewApp
            PlanReviewApp(state / "09_plan.csv").run()
        else:
            err.print(f"[red]nothing to review (no 16_merged.csv or 09_plan.csv in {state})[/red]")
            raise typer.Exit(2)
        return

    if export:
        out_path = out or (state / "19_review.csv")
        n = approval.build_review_csv(merged, out_path)
        _info(f"[green]wrote[/green] {out_path} ({n} rows)")
        return

    if import_:
        approvals = state / "19_approvals.json"
        try:
            n, _ = approval.parse_review_csv(import_, approvals)
        except approval.ApprovalValidationError as e:
            err.print(f"[red]CSV validation failed:[/red] {e}")
            for row, field, issue in getattr(e, "errors", []):
                err.print(f"  row {row} field {field}: {issue}")
            raise typer.Exit(2)
        _info(f"[green]wrote[/green] {approvals} ({n} approvals)")
        return

    err.print("usage: --export | --import FILE | --interactive")
    raise typer.Exit(2)


@app.command()
def approve(
    rule: str = typer.Option(..., "--rule", help='e.g. "jiosaavn>shazam>itunes"'),
    confidence_min: float = typer.Option(0.65, "--confidence-min"),
    bucket: str = typer.Option("review,low", "--bucket"),
) -> None:
    """Non-interactive batch approval by priority rule."""
    cfg = _cfg()
    state = cfg.state_path
    merged = state / "16_merged.csv"
    approvals = state / "19_approvals.json"
    n = approval.batch_rule(merged, approvals, rule)
    _info(f"[green]wrote[/green] {approvals} ({n} approvals)")


@app.command(name="canonical-apply")
def canonical_apply(
    dry_run: bool = typer.Option(False, "--dry-run"),
    no_rename: bool = typer.Option(False, "--no-rename"),
    include_low: bool = typer.Option(False, "--include-low"),
) -> None:
    """Backup + write tags + rename files for auto_apply and approved rows."""
    cfg = _cfg()
    state = cfg.state_path
    ts = _ts()
    snapshot = state / "backups" / f"tag_snapshot_{ts}.json"
    undo_path = state / f"undo_phase18_{ts}.py"

    merged = state / "16_merged.csv"
    diff_csv = state / "17_dryrun_diff.csv"
    canonicalize.build_diff(merged, diff_csv)
    result_auto = canonicalize.apply(
        diff_csv=diff_csv, merged_csv=merged,
        snapshot_path=snapshot, undo_script_path=undo_path,
        dry_run=dry_run, rename=not no_rename, include_low=include_low,
    )

    approvals = state / "19_approvals.json"
    if approvals.exists():
        snap2 = state / "backups" / f"tag_snapshot_phase20_{ts}.json"
        undo2 = state / f"undo_phase20_{ts}.py"
        result_app = canonicalize.apply_approvals(
            merged_csv=merged, approvals_json=approvals,
            snapshot_path=snap2, undo_script_path=undo2,
            dry_run=dry_run, rename=not no_rename,
        )
        _info("[bold]auto-apply[/bold]")
        _emit({
            "files_touched": result_auto.files_touched, "tags_written": result_auto.tags_written,
            "renamed": result_auto.renamed, "guarded": result_auto.guarded,
            "collisions": result_auto.collisions, "errors": result_auto.errors,
            "snapshot": str(result_auto.snapshot_path), "undo": str(result_auto.undo_script_path),
        })
        _info("[bold]approvals[/bold]")
        _emit({
            "files_touched": result_app.files_touched, "tags_written": result_app.tags_written,
            "renamed": result_app.renamed, "guarded": result_app.guarded,
            "collisions": result_app.collisions, "errors": result_app.errors,
            "snapshot": str(result_app.snapshot_path), "undo": str(result_app.undo_script_path),
        })
    else:
        _emit({
            "files_touched": result_auto.files_touched, "tags_written": result_auto.tags_written,
            "renamed": result_auto.renamed, "guarded": result_auto.guarded,
            "collisions": result_auto.collisions, "errors": result_auto.errors,
            "snapshot": str(result_auto.snapshot_path), "undo": str(result_auto.undo_script_path),
        })


@app.command(name="canonical-undo")
def canonical_undo(
    list_: bool = typer.Option(False, "--list"),
    snapshot: Optional[str] = typer.Option(None, "--snapshot"),
    latest: bool = typer.Option(False, "--latest"),
) -> None:
    """Restore tags + filenames from a snapshot."""
    cfg = _cfg()
    state = cfg.state_path
    snaps = backup.list_snapshots(state)
    if list_:
        t = Table(title="snapshots")
        t.add_column("path"); t.add_column("ts"); t.add_column("ops")
        for s in snaps:
            t.add_row(str(s.get("path", "")), str(s.get("ts", "")), str(s.get("ops_count", "")))
        console.print(t); return
    if latest and snaps:
        target = Path(snaps[-1]["path"])
    elif snapshot:
        target = state / "backups" / snapshot
    else:
        err.print("usage: --latest | --snapshot TS"); raise typer.Exit(2)
    if not target.exists():
        err.print(f"[red]snapshot missing: {target}[/red]"); raise typer.Exit(1)
    result = backup.restore_from_snapshot(target)
    _emit(result)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — upgrade
# ─────────────────────────────────────────────────────────────────────────────

@app.command(name="upgrade")
def upgrade_cmd(
    dry_run: bool = typer.Option(False, "--dry-run"),
    staging_dir: Optional[Path] = typer.Option(None, "--staging-dir"),
    cookies: Optional[Path] = typer.Option(None, "--cookies"),
    wvd: Optional[Path] = typer.Option(None, "--wvd"),
) -> None:
    """gamdl ALAC upgrade for files with resolved apple_music_url."""
    cfg = _cfg()
    state = cfg.state_path
    candidates: list[dict] = []
    refp = state / "30_shazam_refingerprint.csv"
    merged = state / "16_merged.csv"
    for csv_path in (refp, merged):
        if not csv_path.exists():
            continue
        with csv_path.open() as f:
            for r in csv.DictReader(f):
                url = (r.get("apple_music_url") or r.get("api_track_view_url") or "").strip()
                if not url: continue
                candidates.append({
                    "path": r.get("source_path") or r.get("path"),
                    "apple_music_url": url,
                    "adam_id": r.get("adam_id", ""),
                    "storefront": r.get("storefront", ""),
                })
        break
    _info(f"upgrade: {len(candidates)} candidates")
    if not candidates:
        return
    result = upgrade.upgrade_batch(
        candidates=candidates,
        music_root=cfg.root_path,
        state_dir=state,
        dry_run=dry_run,
        cookies_path=cookies,
        wvd_path=wvd,
    )
    _emit(result)


@app.command("refingerprint")
def refingerprint_cmd(
    lossy_only: bool = typer.Option(True, "--lossy-only/--all-files"),
    path: Optional[Path] = typer.Option(None, "--path"),
) -> None:
    """Fresh Shazam audio-fingerprint pass on lossy files. Harvests Apple Music URLs."""
    cfg = _cfg()
    state = cfg.state_path
    breaker = CircuitBreaker(name="SHAZAMIO", threshold=cfg.shazam_breaker_threshold, marker_dir=state)
    if breaker.is_tripped():
        err.print(f"[yellow]circuit breaker tripped: {breaker.is_tripped()}[/yellow]")
        raise typer.Exit(0)
    result = refingerprint.refingerprint_lossy(
        music_root=cfg.root_path / "Music",
        state_dir=state,
        lossy_only=lossy_only,
        path_filter=path,
        breaker=breaker,
        sleep_sec=cfg.shazam_inter_request_sleep_sec,
    )
    _emit(result)


@app.command(name="recover-staging")
def recover_staging_cmd(
    staging_dir: Optional[Path] = typer.Option(None, "--staging-dir"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Recover ALAC orphans left by failed/interrupted upgrade runs."""
    cfg = _cfg()
    state = cfg.state_path
    result = refingerprint.recover_staging(state, cfg.root_path, dry_run=dry_run)
    _emit(result)


@app.command(name="permanent-skip-report")
def permanent_skip_report_cmd(
    format_: str = typer.Option("table", "--format", help="table|csv|json"),
) -> None:
    """Report files in permanent-skip state, grouped by reason."""
    cfg = _cfg()
    report = upgrade.permanent_skip_report(cfg.state_path)
    if format_ == "json":
        sys.stdout.write(json.dumps(report, indent=2) + "\n"); return
    if format_ == "csv":
        w = csv.writer(sys.stdout)
        w.writerow(["reason", "path"])
        for reason, items in report.items():
            for item in items:
                w.writerow([reason, item.get("path", "")])
        return
    t = Table(title="permanent skips")
    t.add_column("reason"); t.add_column("count")
    for reason, items in report.items():
        t.add_row(reason, str(len(items)))
    console.print(t)


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

@config_app.command("init")
def config_init() -> None:
    """Interactive setup."""
    acoustid = typer.prompt("AcoustID app API key (blank to skip)", default="", show_default=False)
    mb_email = typer.prompt("MusicBrainz contact email", default="")
    country = typer.prompt("Default country (bollywood|hollywood|auto)", default="bollywood")
    updates = {
        "acoustid": {"api_key": acoustid},
        "musicbrainz": {"contact_email": mb_email, "user_agent": f"musicorg/0.1 ({mb_email or 'anonymous'})"},
        "defaults": {"country": country},
    }
    save_global_config(updates)
    _info(f"[green]wrote[/green] {GLOBAL_CONFIG}")


@config_app.command("set")
def config_set(
    key: str = typer.Argument(...),
    value: str = typer.Argument(...),
) -> None:
    """Set a config key. Key form: section.key (e.g. acoustid.api_key)."""
    if "." not in key:
        err.print("[red]key must be 'section.key'[/red]"); raise typer.Exit(2)
    section, k = key.split(".", 1)
    save_global_config({section: {k: value}})
    _info(f"[green]set[/green] {key}={value}")


@config_app.command("show")
def config_show() -> None:
    """Show effective config."""
    cfg = _cfg()
    t = Table(title=f"config (library={cfg.library_slug})")
    t.add_column("key"); t.add_column("value")
    for k in (
        "acoustid_app_api_key", "musicbrainz_contact", "itunes_country",
        "default_country", "apply_mode", "auto_apply_threshold", "review_threshold",
        "shazam_auto_apply_threshold", "shazam_breaker_threshold",
        "library_slug", "library_root", "state_dir",
    ):
        t.add_row(k, str(getattr(cfg, k, "")))
    console.print(t)
    console.print(f"[dim]global:  {GLOBAL_CONFIG}[/dim]")
    console.print(f"[dim]library: {Path(cfg.state_dir) / 'config.ini'}[/dim]")


if __name__ == "__main__":
    app()
