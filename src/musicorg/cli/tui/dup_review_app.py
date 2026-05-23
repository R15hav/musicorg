"""TUI for hand-curating dedupe winners/losers.

Spec: TERMINAL_APP_PLAN section 8.3. The left pane is a ListView of
duplicate groups, the right pane is a DataTable of the selected group's
members. ``w`` re-anoints the highlighted member as winner; ``s`` skips
a group entirely (all members become standalone winners with no
quarantine). On quit (``q``) we rewrite all three dedupe CSVs.

The app reads the existing 07_* CSVs rather than re-running the
heuristic ``dedupe.group_duplicates`` — by the time a user is reviewing
dedupe results, the machine grouping is the source of truth and we just
want to override individual choices.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Footer, Header, ListItem, ListView, Static


class _GroupItem(ListItem):
    """ListItem that carries the group id for later lookup."""

    def __init__(self, gid: str, label: str) -> None:
        super().__init__(Static(label))
        self.gid: str = gid


class DupReviewApp(App[int]):
    """Interactive dedupe-group review.

    Constructor accepts the state directory; the app reads:
        - ``07_winners.csv``     (one row per file that survives)
        - ``07_duplicates.csv``  (loser rows + ``winner_path``)
        - ``07_groups.csv``      (group summaries)

    On ``q`` (save+quit) all three CSVs are rewritten to reflect the
    user's curated choices. Skipped groups have their members promoted
    to singleton winners.
    """

    BINDINGS = [
        Binding("j", "next_group", "Group +", show=True),
        Binding("k", "prev_group", "Group -", show=True),
        Binding("J", "next_member", "Member +", show=True),
        Binding("K", "prev_member", "Member -", show=True),
        Binding("w", "mark_winner", "Winner", show=True),
        Binding("s", "skip_group", "Skip", show=True),
        Binding("q", "save_quit", "Save+Quit", show=True),
    ]

    CSS = """
    Screen {
        layout: vertical;
    }
    #panes {
        height: 1fr;
    }
    #groups-list {
        width: 36;
        border: solid $primary;
    }
    #members-table {
        width: 1fr;
        border: solid $primary;
    }
    #status-bar {
        height: auto;
        min-height: 1;
        background: $boost;
        padding: 0 1;
    }
    """

    def __init__(self, state_dir: Path) -> None:
        super().__init__()
        self.state_dir: Path = Path(state_dir)
        self.winners_path: Path = self.state_dir / "07_winners.csv"
        self.duplicates_path: Path = self.state_dir / "07_duplicates.csv"
        self.groups_path: Path = self.state_dir / "07_groups.csv"

        # Field-name snapshots so we can round-trip unknown columns.
        self._winners_fields: list[str] = []
        self._dup_fields: list[str] = []
        self._groups_fields: list[str] = []

        # Source rows by group id.
        # gid -> list of member dicts (each has dup_role set to
        # 'winner' / 'loser' / 'singleton' / 'skipped').
        self._members_by_group: dict[str, list[dict[str, str]]] = defaultdict(list)
        # Original singleton rows (carried through saves untouched).
        self._singletons: list[dict[str, str]] = []
        # Ordered list of multi-member group ids.
        self._group_ids: list[str] = []
        # Group summary rows keyed by gid.
        self._group_summaries: dict[str, dict[str, str]] = {}
        # Per-group skip flag (overrides individual w choices on save).
        self._skipped: set[str] = set()

        self._current_group_index: int = 0
        self._current_member_index: int = 0
        self._exit_code: int = 0

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="panes"):
            yield ListView(id="groups-list")
            yield DataTable(id="members-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._load_data()
        table = self.query_one("#members-table", DataTable)
        for col in ("role", "path", "bitrate_kbps", "size_bytes", "title", "artist"):
            table.add_column(col, key=col)
        self._populate_group_list()
        self._render_members()
        self._set_status(
            f"Loaded {len(self._group_ids)} multi-member group(s) "
            f"and {len(self._singletons)} singleton(s)"
        )

    # ------------------------------------------------------------------ #
    # I/O
    # ------------------------------------------------------------------ #

    def _read_csv(self, path: Path) -> tuple[list[str], list[dict[str, str]]]:
        if not path.exists():
            return [], []
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            return list(reader.fieldnames or []), [dict(r) for r in reader]

    def _load_data(self) -> None:
        self._winners_fields, winners = self._read_csv(self.winners_path)
        self._dup_fields, losers = self._read_csv(self.duplicates_path)
        self._groups_fields, groups = self._read_csv(self.groups_path)

        for g in groups:
            gid = (g.get("group") or "").strip()
            if not gid:
                continue
            self._group_summaries[gid] = g

        # Singletons live only in winners with empty dup_group.
        for w in winners:
            gid = (w.get("dup_group") or "").strip()
            role = (w.get("dup_role") or "").strip()
            if not gid:
                # Either an explicit singleton or a winner sans group.
                self._singletons.append(w)
                continue
            self._members_by_group[gid].append({**w, "dup_role": role or "winner"})

        for loser in losers:
            gid = (loser.get("dup_group") or "").strip()
            if not gid:
                # Orphaned loser — treat as singleton for safety.
                self._singletons.append(loser)
                continue
            self._members_by_group[gid].append({**loser, "dup_role": "loser"})

        # Stable ordering: groups CSV first, then any orphans we found.
        seen: set[str] = set()
        for g in groups:
            gid = (g.get("group") or "").strip()
            if gid and gid in self._members_by_group and gid not in seen:
                self._group_ids.append(gid)
                seen.add(gid)
        for gid in self._members_by_group:
            if gid not in seen:
                self._group_ids.append(gid)
                seen.add(gid)

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def _populate_group_list(self) -> None:
        lv = self.query_one("#groups-list", ListView)
        lv.clear()
        for gid in self._group_ids:
            members = self._members_by_group.get(gid, [])
            summary = self._group_summaries.get(gid, {})
            label_key = summary.get("key") or ""
            tag = " [skip]" if gid in self._skipped else ""
            label = f"{gid}  ({len(members)})  {label_key}{tag}"
            lv.append(_GroupItem(gid, label))
        if self._group_ids:
            lv.index = 0

    def _render_members(self) -> None:
        table = self.query_one("#members-table", DataTable)
        table.clear()
        if not self._group_ids:
            return
        gid = self._group_ids[self._current_group_index]
        members = self._members_by_group.get(gid, [])
        for m in members:
            table.add_row(
                m.get("dup_role", ""),
                m.get("path", ""),
                str(m.get("bitrate_kbps", "")),
                str(m.get("size_bytes", "")),
                m.get("title", ""),
                m.get("artist", ""),
                key=m.get("path", ""),
            )
        if members:
            self._current_member_index = min(self._current_member_index, len(members) - 1)
            table.move_cursor(row=self._current_member_index, column=0)

    def _set_status(self, msg: str) -> None:
        self.query_one("#status-bar", Static).update(msg)

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #

    def action_next_group(self) -> None:
        if not self._group_ids:
            return
        self._current_group_index = min(self._current_group_index + 1, len(self._group_ids) - 1)
        self._current_member_index = 0
        self.query_one("#groups-list", ListView).index = self._current_group_index
        self._render_members()

    def action_prev_group(self) -> None:
        if not self._group_ids:
            return
        self._current_group_index = max(self._current_group_index - 1, 0)
        self._current_member_index = 0
        self.query_one("#groups-list", ListView).index = self._current_group_index
        self._render_members()

    def action_next_member(self) -> None:
        if not self._group_ids:
            return
        gid = self._group_ids[self._current_group_index]
        members = self._members_by_group.get(gid, [])
        if not members:
            return
        self._current_member_index = min(self._current_member_index + 1, len(members) - 1)
        table = self.query_one("#members-table", DataTable)
        table.move_cursor(row=self._current_member_index, column=0)

    def action_prev_member(self) -> None:
        if not self._group_ids:
            return
        self._current_member_index = max(self._current_member_index - 1, 0)
        table = self.query_one("#members-table", DataTable)
        table.move_cursor(row=self._current_member_index, column=0)

    @on(ListView.Highlighted, "#groups-list")
    def _on_group_highlight(self, event: ListView.Highlighted) -> None:
        item = event.item
        if not isinstance(item, _GroupItem):
            return
        try:
            self._current_group_index = self._group_ids.index(item.gid)
        except ValueError:
            return
        self._current_member_index = 0
        self._render_members()

    # ------------------------------------------------------------------ #
    # Editing
    # ------------------------------------------------------------------ #

    def action_mark_winner(self) -> None:
        if not self._group_ids:
            return
        gid = self._group_ids[self._current_group_index]
        members = self._members_by_group.get(gid, [])
        if not members:
            return
        # Promote the highlighted member, demote everyone else.
        for i, m in enumerate(members):
            m["dup_role"] = "winner" if i == self._current_member_index else "loser"
        # If the user marks a winner inside a previously-skipped group,
        # they've re-engaged with it — unset the skip flag.
        self._skipped.discard(gid)
        self._populate_group_list()
        # Keep the group selection sticky after the list re-render.
        self.query_one("#groups-list", ListView).index = self._current_group_index
        self._render_members()
        self._set_status(
            f"{gid}: winner -> {members[self._current_member_index].get('path', '')}"
        )

    def action_skip_group(self) -> None:
        if not self._group_ids:
            return
        gid = self._group_ids[self._current_group_index]
        if gid in self._skipped:
            self._skipped.discard(gid)
            self._set_status(f"{gid}: unskipped")
        else:
            self._skipped.add(gid)
            self._set_status(f"{gid}: skipped (all members will be standalone winners)")
        self._populate_group_list()
        self.query_one("#groups-list", ListView).index = self._current_group_index
        self._render_members()

    # ------------------------------------------------------------------ #
    # Save
    # ------------------------------------------------------------------ #

    def _collect_outputs(self) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
        winners: list[dict[str, str]] = []
        for s in self._singletons:
            row = dict(s)
            row.setdefault("dup_group", "")
            row.setdefault("dup_role", "singleton")
            winners.append(row)

        losers: list[dict[str, str]] = []
        groups_out: list[dict[str, str]] = []

        for gid in self._group_ids:
            members = self._members_by_group.get(gid, [])
            if not members:
                continue
            if gid in self._skipped:
                # All members survive without quarantine.
                for m in members:
                    row = dict(m)
                    row["dup_group"] = ""
                    row["dup_role"] = "singleton"
                    row.pop("winner_path", None)
                    winners.append(row)
                continue

            # Pick the user-marked winner; fall back to the first member
            # if somehow nothing is marked (defensive — UI guarantees one).
            winner_idx = next(
                (i for i, m in enumerate(members) if m.get("dup_role") == "winner"),
                0,
            )
            winner = members[winner_idx]
            losers_in_group = [m for i, m in enumerate(members) if i != winner_idx]

            winner_row = dict(winner)
            winner_row["dup_group"] = gid
            winner_row["dup_role"] = "winner"
            winner_row.pop("winner_path", None)
            winners.append(winner_row)

            for loser in losers_in_group:
                lr = dict(loser)
                lr["dup_group"] = gid
                lr["dup_role"] = "loser"
                lr["winner_path"] = winner.get("path", "")
                losers.append(lr)

            existing_summary = self._group_summaries.get(gid, {})
            groups_out.append({
                **existing_summary,
                "group": gid,
                "count": str(len(members)),
                "winner": winner.get("path", ""),
                "losers": " | ".join(l.get("path", "") for l in losers_in_group),
            })

        return winners, losers, groups_out

    def _write_csv(self, path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in rows:
                writer.writerow({k: str(r.get(k, "") or "") for k in fieldnames})

    def _save_outputs(self) -> bool:
        winners, losers, groups_out = self._collect_outputs()

        win_fields = self._winners_fields or list(winners[0].keys()) if winners else []
        for c in ("dup_group", "dup_role"):
            if c not in win_fields:
                win_fields.append(c)

        dup_fields = self._dup_fields or (list(losers[0].keys()) if losers else [])
        for c in ("dup_group", "dup_role", "winner_path"):
            if c not in dup_fields:
                dup_fields.append(c)

        grp_fields = self._groups_fields or (
            list(groups_out[0].keys()) if groups_out else
            ["group", "key", "duration_bucket", "count", "winner",
             "winner_bitrate", "winner_completeness", "losers"]
        )

        try:
            self._write_csv(self.winners_path, win_fields, winners)
            self._write_csv(self.duplicates_path, dup_fields, losers)
            self._write_csv(self.groups_path, grp_fields, groups_out)
        except Exception as exc:  # pragma: no cover - filesystem
            self._set_status(f"Save failed: {exc}")
            return False
        return True

    def action_save_quit(self) -> None:
        if self._save_outputs():
            self.exit(0)
