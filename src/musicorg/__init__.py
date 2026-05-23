"""musicorg — content-addressed music library organizer.

The public Python API. Names exported here follow SemVer after v1.0.0.
Names starting with underscore are internal and may change between minor
versions.

The CLI lives in a separate package, ``musicorg_cli``, which is a reference
consumer of this library — install it with ``pip install musicorg[cli]``.
See ``examples/`` at the repo root for additional integration patterns.

See PUBLIC_API.md at the project root for the full contract.
"""

__version__ = "0.2.0"

# Configuration ----------------------------------------------------------------
from .config import (
    Config,
    load_config,
    save_library_config,
    save_global_config,
    ensure_state_dir,
)

# Runtime binaries -------------------------------------------------------------
from ._binaries import set_binary_paths

# Models -----------------------------------------------------------------------
from .models import (
    Track,
    ResolvedTrack,
    TierMatch,
    ApplyResult,
    ProgressEvent,
    SkipReason,
)

# Identity ---------------------------------------------------------------------
from .identity import (
    audio_stream_sha256,
    identity_quality,
)

# Core phases ------------------------------------------------------------------
from .scan import scan, write_tags_csv, compute_stats
from .dedupe import group_duplicates, read_tags_csv, write_dedupe_outputs
from .resolve import resolve_winners, reconcile, detect_country
from .canonicalize import (
    apply as apply_canonical,
    apply_approvals,
    build_diff,
    canonicalize_library,
    pick_source_for_row,
)
from .executor import execute_plan, run_undo
from .planner import plan, build_destination, decade_of

# Backup + undo ----------------------------------------------------------------
from .backup import (
    SnapshotStore,
    DEFAULT_EXCLUDED_FRAMES,
    generate_undo_script,
    snapshot_tags,
    list_snapshots,
    restore_from_snapshot,
)

# Tag I/O ----------------------------------------------------------------------
from .tags import read as read_tags, write as write_tags, snapshot as snapshot_tags_raw

# Cleaning utilities -----------------------------------------------------------
from .clean import (
    strip_junk,
    clean_for_query,
    safe,
    safe_filename,
    normalize_key,
    normalize_album,
    normalize_year,
    parse_folder,
    parse_track_num,
    title_from_filename,
    strip_year_suffix,
    junkiness,
    SITE_JUNK_PATTERNS,
)

# Approval / review gate -------------------------------------------------------
from .approval import (
    ApprovalValidationError,
    build_review_csv,
    parse_review_csv,
    batch_rule,
    summarise_buckets,
)

# Lookup (per-tier and chained) ------------------------------------------------
from .lookup import (
    chain,
    decide,
    score_match,
    score_shazam,
    extract_apple_music_url,
    itunes_search,
    jiosaavn_search,
    shazam_recognize,
    CircuitBreaker,
)

# Extensions (extension authors implement UpgradeExtension) --------------------
from .extensions import (
    UpgradeCandidate,
    UpgradeResult,
    UpgradeExtension,
    UpgradeStatus,
    PreflightResult,
    SUPPORTED_PROTOCOL_VERSIONS,
)

# UpgradeStatus is a Literal type alias; Literal objects have no __doc__ slot.
# Attach one so the docstring check passes and help() is informative.
UpgradeStatus.__doc__ = (
    "Outcome categories an UpgradeExtension may report for one candidate. "
    "Values: 'upgraded', 'lossy_only_confirmed', 'alac_unservable', "
    "'remix_dj', 'region_blocked', 'transient_error', 'skipped'. "
    "See extensions/protocol.py for full semantics."
)

# Upgrade orchestration (upgrade.py is owned by Wave 3A — do not modify) ------
from .upgrade import upgrade_one, upgrade_batch, permanent_skip_report

__all__ = [
    # Configuration
    "Config",
    "load_config",
    "save_library_config",
    "save_global_config",
    "ensure_state_dir",
    # Runtime binaries
    "set_binary_paths",
    # Models
    "Track",
    "ResolvedTrack",
    "TierMatch",
    "ApplyResult",
    "ProgressEvent",
    "SkipReason",
    # Identity
    "audio_stream_sha256",
    "identity_quality",
    # Scan phase
    "scan",
    "write_tags_csv",
    "compute_stats",
    # Dedupe phase
    "group_duplicates",
    "read_tags_csv",
    "write_dedupe_outputs",
    # Resolve phase
    "resolve_winners",
    "reconcile",
    "detect_country",
    # Canonicalize phase
    "apply_canonical",
    "apply_approvals",
    "build_diff",
    "canonicalize_library",
    "pick_source_for_row",
    # Execute phase
    "execute_plan",
    "run_undo",
    # Planner
    "plan",
    "build_destination",
    "decade_of",
    # Backup + undo
    "SnapshotStore",
    "DEFAULT_EXCLUDED_FRAMES",
    "generate_undo_script",
    "snapshot_tags",
    "list_snapshots",
    "restore_from_snapshot",
    # Tag I/O
    "read_tags",
    "write_tags",
    "snapshot_tags_raw",
    # Cleaning utilities
    "strip_junk",
    "clean_for_query",
    "safe",
    "safe_filename",
    "normalize_key",
    "normalize_album",
    "normalize_year",
    "parse_folder",
    "parse_track_num",
    "title_from_filename",
    "strip_year_suffix",
    "junkiness",
    "SITE_JUNK_PATTERNS",
    # Approval / review gate
    "ApprovalValidationError",
    "build_review_csv",
    "parse_review_csv",
    "batch_rule",
    "summarise_buckets",
    # Lookup
    "chain",
    "decide",
    "score_match",
    "score_shazam",
    "extract_apple_music_url",
    "itunes_search",
    "jiosaavn_search",
    "shazam_recognize",
    "CircuitBreaker",
    # Extensions
    "UpgradeCandidate",
    "UpgradeResult",
    "UpgradeExtension",
    "UpgradeStatus",
    "PreflightResult",
    "SUPPORTED_PROTOCOL_VERSIONS",
    # Upgrade orchestration (provisional — may change shape in v1.0)
    "upgrade_one",
    "upgrade_batch",
    "permanent_skip_report",
]
