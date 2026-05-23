"""Human-review CSV: generate, validate, and convert to approvals JSON.

Phase 19 of the original pipeline. The review CSV sits between auto-apply
(Phase 18) and the remainder-apply (Phase 20): the user edits an
``approve`` column to choose itunes/jiosaavn/shazam/manual/skip per row,
then we serialise that decision into ``19_approvals.json`` for the apply
step. Strict CSV validation catches typos and missing manual titles BEFORE
any file is mutated.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Iterable


_APPROVE_WHITELIST: frozenset[str] = frozenset({"", "itunes", "jiosaavn", "shazam", "manual", "skip"})

_REVIEW_FIELDS: list[str] = [
    "row", "decision", "confidence",
    "source_path", "current_folder", "current_filename",
    "cur_title", "cur_artist", "cur_album", "cur_year",
    "itunes_title", "itunes_artist", "itunes_album", "itunes_year",
    "jio_title", "jio_artist", "jio_album", "jio_year",
    "shazam_title", "shazam_artist", "shazam_album", "shazam_year",
    "approve",
    "manual_title", "manual_artist", "manual_album", "manual_year",
]

_DECISION_ORDER: dict[str, int] = {"review": 0, "low": 1, "no_match": 2}
_DEFAULT_BUCKETS: list[str] = ["review", "low", "no_match"]


class ApprovalValidationError(Exception):
    """Raised when the user-edited review CSV fails validation.

    ``errors`` carries a list of ``(row_num, field, issue)`` tuples for
    CLI rendering. ``row_num`` is 1-indexed and matches the CSV's own
    ``row`` column so users can locate offenders quickly.
    """

    def __init__(self, msg: str, errors: list[tuple[int, str, str]] | None = None) -> None:
        super().__init__(msg)
        self.errors: list[tuple[int, str, str]] = errors or []


def _read_merged(merged_csv: Path) -> list[dict]:
    with Path(merged_csv).open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _filter_for_review(rows: Iterable[dict], promote_set: set[str] | None) -> list[dict]:
    promote_set = promote_set or set()
    work = []
    for r in rows:
        # Apply promotion BEFORE bucketing so promoted rows fall out of review.
        if r.get("source_path") in promote_set and r.get("decision") != "auto_apply":
            r = dict(r)
            r["decision"] = "auto_apply"
        work.append(r)
    return [r for r in work if r.get("decision") in _DEFAULT_BUCKETS]


def build_review_csv(
    merged_csv: Path,
    out_path: Path,
    promote_set: set[str] | None = None,
) -> int:
    """Write the human-review CSV. Returns the row count written."""
    merged_csv = Path(merged_csv)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = _read_merged(merged_csv)
    unresolved = _filter_for_review(rows, promote_set)
    unresolved.sort(key=lambda r: (
        _DECISION_ORDER.get(r.get("decision", ""), 99),
        -_safe_float(r.get("confidence", "")),
    ))

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_REVIEW_FIELDS)
        w.writeheader()
        for i, r in enumerate(unresolved, 1):
            itunes_t, itunes_a, itunes_alb, itunes_y = _isolate_itunes(r)
            source_path = r.get("source_path", "")
            w.writerow({
                "row": i,
                "decision": r.get("decision", ""),
                "confidence": r.get("confidence", ""),
                "source_path": source_path,
                "current_folder": Path(source_path).parent.name if source_path else "",
                "current_filename": Path(source_path).name if source_path else "",
                "cur_title": r.get("cur_title", ""),
                "cur_artist": r.get("cur_artist", ""),
                "cur_album": r.get("cur_album", ""),
                "cur_year": r.get("cur_year", ""),
                "itunes_title": itunes_t,
                "itunes_artist": itunes_a,
                "itunes_album": itunes_alb,
                "itunes_year": itunes_y,
                "jio_title": r.get("jio_title", ""),
                "jio_artist": r.get("jio_artist", ""),
                "jio_album": r.get("jio_album", ""),
                "jio_year": r.get("jio_year", ""),
                "shazam_title": r.get("shazam_title", ""),
                "shazam_artist": r.get("shazam_artist", ""),
                "shazam_album": r.get("shazam_album", ""),
                "shazam_year": r.get("shazam_year", ""),
                "approve": "",
                "manual_title": "",
                "manual_artist": "",
                "manual_album": "",
                "manual_year": "",
            })
    return len(unresolved)


def _isolate_itunes(r: dict) -> tuple[str, str, str, str]:
    """Pull out the iTunes view when ``api_*`` was overridden by jio/shazam."""
    jio_title = (r.get("jio_title") or "").strip()
    shazam_title = (r.get("shazam_title") or "").strip()
    api_title = (r.get("api_title") or "").strip()
    if shazam_title and api_title == shazam_title:
        return "", "", "", ""
    if jio_title and api_title == jio_title:
        return "", "", "", ""
    return (api_title, r.get("api_artist", ""), r.get("api_album", ""), r.get("api_year", ""))


def _safe_float(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def parse_review_csv(in_path: Path, approvals_out: Path) -> tuple[int, list[dict]]:
    """Strict-validate the user-edited review CSV and emit ``19_approvals.json``.

    Validation rules:
        - The header MUST contain every column in ``_REVIEW_FIELDS``.
        - Each row's column count must match the header (catches stray
          commas that shift the ``approve`` column).
        - ``approve`` must be in ``_APPROVE_WHITELIST``.
        - When ``approve == "manual"``, ``manual_title`` must be non-empty.
        - ``source_path`` must be present on every row.

    On failure: raises ``ApprovalValidationError`` with all collected
    issues. On success: writes a JSON list of
    ``{source_path, pick, manual_fields}`` and returns
    ``(approval_count, approvals_list)``. ``manual_fields`` is omitted when
    pick != "manual".
    """
    in_path = Path(in_path)
    approvals_out = Path(approvals_out)
    approvals_out.parent.mkdir(parents=True, exist_ok=True)

    errors: list[tuple[int, str, str]] = []
    approvals: list[dict] = []

    with in_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            raise ApprovalValidationError("empty review CSV", errors=[(0, "header", "missing")])

        expected = _REVIEW_FIELDS
        missing = [c for c in expected if c not in header]
        if missing:
            raise ApprovalValidationError(
                f"missing required columns: {missing}",
                errors=[(0, c, "missing column") for c in missing],
            )
        col_index = {name: header.index(name) for name in expected}
        expected_cols = len(header)

        for line_num, raw_row in enumerate(reader, start=2):
            csv_row_label = line_num - 1  # the "row" column is 1-indexed
            if len(raw_row) != expected_cols:
                errors.append((csv_row_label, "row", f"column count {len(raw_row)} != {expected_cols}"))
                continue

            def col(name: str) -> str:
                return raw_row[col_index[name]].strip()

            source_path = col("source_path")
            if not source_path:
                errors.append((csv_row_label, "source_path", "empty"))
                continue

            approve = col("approve").lower()
            if approve not in _APPROVE_WHITELIST:
                errors.append((csv_row_label, "approve", f"unknown value {approve!r}"))
                continue

            if approve == "" or approve == "skip":
                continue

            entry: dict = {"source_path": source_path, "pick": approve}
            if approve == "manual":
                manual_title = col("manual_title")
                if not manual_title:
                    errors.append((csv_row_label, "manual_title", "required when approve=manual"))
                    continue
                entry["manual_fields"] = {
                    "title": manual_title,
                    "artist": col("manual_artist"),
                    "album": col("manual_album"),
                    "year": col("manual_year"),
                }
            approvals.append(entry)

    if errors:
        raise ApprovalValidationError(
            f"validation failed with {len(errors)} issue(s)",
            errors=errors,
        )

    approvals_out.write_text(json.dumps(approvals, indent=2), encoding="utf-8")
    return len(approvals), approvals


def batch_rule(
    merged_csv: Path,
    approvals_out: Path,
    rule: str,
    *,
    confidence_min: float = 0.65,
    buckets: list[str] | None = None,
) -> int:
    """Apply a non-interactive priority rule, e.g. ``"jiosaavn>shazam>itunes"``.

    For each row in the named ``buckets`` (default: ``review``, ``low``,
    ``no_match``), pick the first tier in the rule whose data is non-empty
    AND whose tier-specific confidence column is >= ``confidence_min``.
    Rows where no tier qualifies are skipped (no entry in approvals).

    Returns the approval count written. The confidence column consulted per
    tier is ``<tier>_confidence`` when present; otherwise the row-level
    ``confidence`` (treated as a global score) is used as a fallback.
    """
    merged_csv = Path(merged_csv)
    approvals_out = Path(approvals_out)
    approvals_out.parent.mkdir(parents=True, exist_ok=True)

    tiers = [t.strip().lower() for t in rule.split(">") if t.strip()]
    if not tiers:
        raise ValueError(f"invalid rule: {rule!r}")
    for t in tiers:
        if t not in {"itunes", "jiosaavn", "shazam"}:
            raise ValueError(f"unknown tier in rule: {t!r}")

    target_buckets = set(buckets or _DEFAULT_BUCKETS)
    rows = _read_merged(merged_csv)
    candidates = [r for r in rows if r.get("decision") in target_buckets]

    approvals: list[dict] = []
    for r in candidates:
        source_path = r.get("source_path", "")
        if not source_path:
            continue
        pick = _pick_tier(r, tiers, confidence_min)
        if pick:
            approvals.append({"source_path": source_path, "pick": pick})

    approvals_out.write_text(json.dumps(approvals, indent=2), encoding="utf-8")
    return len(approvals)


def _pick_tier(row: dict, tiers: list[str], confidence_min: float) -> str:
    row_conf = _safe_float(row.get("confidence", ""))
    for t in tiers:
        title_col = {"itunes": "api_title", "jiosaavn": "jio_title", "shazam": "shazam_title"}[t]
        conf_col = f"{t}_confidence"
        title = (row.get(title_col) or "").strip()
        if not title:
            continue
        if conf_col in row:
            tier_conf = _safe_float(row.get(conf_col, ""))
        else:
            tier_conf = row_conf
        if tier_conf >= confidence_min:
            return t
    return ""


def summarise_buckets(merged_csv: Path, promote_set: set[str] | None = None) -> dict:
    """Bucket counts for the review queue. Cheap call for CLI/status output."""
    rows = _read_merged(merged_csv)
    work = _filter_for_review(rows, promote_set)
    return dict(Counter(r.get("decision", "") for r in work))
