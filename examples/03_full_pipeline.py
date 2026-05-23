"""Example 03 — full pipeline (dry-run by default).

Runs the same five-phase flow the CLI uses:

    Phase 1  scan       — walk the library, read tags, compute fingerprints
    Phase 2  dedupe     — cluster duplicates by title+artist+duration
    Phase 3  resolve    — merge folder/tag/filename evidence into canonical records
    Phase 4  plan       — compute target paths under Music/<country>/<decade>/...
    Phase 5  (dry-run)  — show what apply_canonical would do without writing

Phases that touch the filesystem (apply tags, move files) are skipped unless
you pass --apply.  This means you can run this against any folder safely.

Run:
    python 03_full_pipeline.py                       # uses fixture library
    python 03_full_pipeline.py /path/to/music
    python 03_full_pipeline.py /path/to/music --apply  # writes tags + moves files
"""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

from musicorg import (
    load_config,
    ensure_state_dir,
    scan,
    write_tags_csv,
    compute_stats,
    read_tags_csv,
    group_duplicates,
    write_dedupe_outputs,
    resolve_winners,
    plan,
    build_diff,
    apply_canonical,
    ProgressEvent,
)


# A simple one-line progress printer shared across phases.
def _progress(ev: ProgressEvent) -> None:
    marker = "!" if ev.error else " "
    path_tail = ev.path[-55:] if len(ev.path) > 55 else ev.path
    print(f"\r[{marker}] {ev.phase:10} {ev.current:4}/{ev.total:<4}  {path_tail}", end="", flush=True)


def _nl() -> None:
    print()


def main(music_root: str, apply: bool) -> int:
    # -------------------------------------------------------------------------
    # Setup — every example gets its own state_root to avoid cross-contamination.
    # In a real app this would be something like ~/.local/share/myapp/musicorg/.
    # -------------------------------------------------------------------------
    cfg = load_config(state_root=Path("/tmp/musicorg-example-03"))
    state_dir = ensure_state_dir(cfg)  # creates state/, backups/, logs/ subdirs

    root = Path(music_root)
    print(f"library root : {root}")
    print(f"state root   : {state_dir}")
    print()

    # -------------------------------------------------------------------------
    # Phase 1 — scan: walk the tree and compute fingerprints.
    # Output: list[Track] and 01_tags.csv.
    # -------------------------------------------------------------------------
    print("Phase 1 — scan")
    tracks = scan(cfg, root=root, progress=_progress)
    _nl()

    tags_csv = state_dir / "01_tags.csv"
    stats = write_tags_csv(tracks, tags_csv)
    scan_stats = compute_stats(tracks)
    print(f"  {len(tracks)} files  mutagen={scan_stats.mutagen_ok}  ffprobe={scan_stats.ffprobe_ok}  none={scan_stats.none}")
    print()

    # -------------------------------------------------------------------------
    # Phase 2 — dedupe: cluster by normalized title+artist+duration bucket.
    # Output: 07_winners.csv, 07_duplicates.csv, 07_groups.csv.
    # -------------------------------------------------------------------------
    print("Phase 2 — dedupe")
    rows = read_tags_csv(tags_csv)
    winners, losers, groups = group_duplicates(rows)
    dedupe_paths = write_dedupe_outputs(winners, losers, groups, state_dir)
    print(f"  {len(winners)} winners  {len(losers)} duplicates  {len(groups)} groups")
    print()

    # -------------------------------------------------------------------------
    # Phase 3 — resolve: merge folder/tag/filename evidence.
    # Output: 08_resolved.csv.
    # -------------------------------------------------------------------------
    print("Phase 3 — resolve")
    resolved_csv = state_dir / "08_resolved.csv"
    resolve_stats = resolve_winners(dedupe_paths["winners"], resolved_csv)
    conf = resolve_stats.get("confidence", {})
    country = resolve_stats.get("country", {})
    print(f"  {resolve_stats['total']} rows  confidence={dict(conf)}  country={dict(country)}")
    print()

    # -------------------------------------------------------------------------
    # Phase 4 — plan: compute target paths under Music/<country>/<decade>/...
    # Output: 09_plan.csv.
    # -------------------------------------------------------------------------
    print("Phase 4 — plan")
    plan_csv = state_dir / "09_plan.csv"
    # music_root is where the Music/ subdirectory tree will be rooted.
    # For this example we put it under the state_dir so nothing escapes /tmp.
    plan_stats = plan(resolved_csv, plan_csv, music_root=state_dir / "Music")
    print(f"  {plan_stats['total']} rows  by_country={plan_stats.get('by_country', {})}  by_category={plan_stats.get('by_category', {})}")
    print()

    # -------------------------------------------------------------------------
    # Phase 5 — canonicalize (dry-run unless --apply is passed).
    # build_diff reads the merged lookup CSV (16_merged.csv) when it exists.
    # Without a lookup step the merged CSV doesn't exist yet, so we skip the
    # diff phase here and note what would happen.
    # -------------------------------------------------------------------------
    print("Phase 5 — canonicalize (dry-run)")
    merged_csv = state_dir / "16_merged.csv"
    if not merged_csv.exists():
        print(f"  16_merged.csv not present — skipping diff/apply.")
        print(f"  In a full pipeline, run the lookup phase first (chain() per winner),")
        print(f"  then build_diff() and apply_canonical() to write tags.")
        print()
    else:
        diff_csv = state_dir / "17_dryrun_diff.csv"
        diff_stats = build_diff(merged_csv, diff_csv)
        print(f"  diff: {diff_stats}")
        if apply:
            snapshot = state_dir / "backups" / "snapshot.db"
            undo = state_dir / "backups" / "undo.sh"
            result = apply_canonical(
                diff_csv, merged_csv, snapshot, undo,
                dry_run=False, progress=_progress,
            )
            _nl()
            print(f"  applied: {result}")
        else:
            print(f"  (pass --apply to write tags and rename files)")
        print()

    print("pipeline complete.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="musicorg full pipeline example")
    parser.add_argument("music_root", nargs="?",
                        default=str(Path("../tests/fixtures/library-small").resolve()),
                        help="path to the music folder to scan")
    parser.add_argument("--apply", action="store_true",
                        help="write tags and rename files (default: dry-run)")
    args = parser.parse_args()
    sys.exit(main(args.music_root, args.apply))
