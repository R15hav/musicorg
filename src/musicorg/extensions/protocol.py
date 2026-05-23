"""Extension contract for upgrade plugins (gamdl, future Qobuz/Tidal/local-FLAC).

The core publishes UpgradeCandidate rows and consumes UpgradeResult rows.
Extensions own their own staging, wrapper lifecycle, retry, and undo.
Neither side touches the other's state files. This is the anti-corruption layer.

See _organizer/optimization.md "The core <-> extension contract" and CONTRACT-1..4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Protocol, runtime_checkable


SUPPORTED_PROTOCOL_VERSIONS: frozenset[str] = frozenset({"1.0"})


@dataclass(frozen=True)
class UpgradeCandidate:
    """Read-only canonical record published to upgrade extensions.

    Identity is the audio-stream sha256 — extensions never key on path.
    The core constructs these rows from `04_upgradable.csv` and hands them
    to the active extension one at a time. Frozen so extensions cannot
    mutate state that the core considers authoritative.

    Attributes:
        fingerprint_sha256: Primary key; sha256 of the decoded audio stream.
        current_path: Current on-disk location of the lossy original
            (post-Phase-3 organize, before upgrade).
        canonical_title: Cleaned, canonicalized track title.
        canonical_artist: Cleaned, canonicalized primary artist.
        canonical_album: Cleaned, canonicalized album name.
        canonical_year: Release year as a string (CSV-friendly).
        apple_music_url: Normalized https URL with `?i=<adam_id>`.
        adam_id: Apple Music song-level identifier extracted from the URL.
        url_storefront: Storefront code parsed from the URL (e.g. "us").
        home_storefront: The account's home storefront code; extensions
            may use this to decide whether to retry under a different
            storefront when a track is region-blocked.
        listed_audio_traits: Raw `audioTraits` list returned by amp-api.
            Not trustworthy on its own — see GAMDL-2 in optimization.md.
        listed_lossless: Convenience boolean: any-of
            {"lossless", "hi-res-lossless"} appears in `listed_audio_traits`.
        skip_reason: Pre-set by the core when the candidate is already known
            to be unrecoverable. Extensions should still receive these rows
            for visibility but typically short-circuit in `supports()`.
    """

    fingerprint_sha256: str
    current_path: str
    canonical_title: str
    canonical_artist: str
    canonical_album: str
    canonical_year: str
    apple_music_url: str
    adam_id: str
    url_storefront: str
    home_storefront: str
    listed_audio_traits: list[str]
    listed_lossless: bool
    skip_reason: Optional[str] = None


UpgradeStatus = Literal[
    "upgraded",
    "lossy_only_confirmed",
    "alac_unservable",
    "remix_dj",
    "region_blocked",
    "transient_error",
    "skipped",
]
"""Outcome categories an extension may report back for a single candidate.

- ``upgraded``: A lossless file was acquired and placed; the original was
  quarantined. ``new_path`` and ``quarantine_path`` must be populated.
- ``lossy_only_confirmed``: Apple Music serves only a lossy version of this
  track even though it may be listed as lossless. Permanent skip — see
  CORE-2's ``LOSSY_ONLY_ON_APPLE`` mapping.
- ``alac_unservable``: ``audioTraits`` advertises lossless but the served
  stream is not actually ALAC/FLAC. Permanent skip.
- ``remix_dj``: The track is a remix or DJ edit that is not available on
  Apple Music as the matched ``adam_id``. Permanent skip (set by core
  pre-flight in most cases; extensions may also detect mid-upgrade).
- ``region_blocked``: The track is not available in the active storefront.
  Extension may retry against ``home_storefront`` before reporting this.
- ``transient_error``: A retriable failure (network, wrapper crash,
  timeout). The core will keep the row in ``04_upgradable.csv`` for the
  next run.
- ``skipped``: Extension declined to process this candidate (e.g.,
  ``supports()`` returned False, or ``skip_reason`` was already set).
"""


@dataclass
class UpgradeResult:
    """What the extension reports back to the core for one candidate.

    Mutability is intentional: extensions build these incrementally during
    the upgrade loop and may amend fields before returning. Once handed
    back to the core, the core treats it as a read-only authority record
    (CONTRACT-1: extension result authority).

    Attributes:
        fingerprint_sha256: Must equal the candidate's fingerprint. The
            core joins by this key, not by path.
        status: One of :data:`UpgradeStatus`. Drives the core's mapping
            to ``SkipReason`` in ``canonical.csv``.
        new_path: Absolute path of the newly placed lossless file, when
            ``status == "upgraded"``.
        quarantine_path: Absolute path where the original lossy file was
            moved, when ``status == "upgraded"``.
        new_codec: Codec of the new file (typically ``"alac"``).
        new_bits: Bit depth of the new audio stream (e.g. 16 or 24).
        new_rate_hz: Sample rate of the new audio stream in Hz.
        new_size_bytes: Size on disk of the new file in bytes.
        elapsed_sec: Wall-clock time spent on this candidate, for telemetry.
        error_detail: Short, log-safe error description for non-success
            statuses. Never include secrets, cookies, or wrapper internals.
        permanent: Extension's signal that this outcome is final. When True,
            the core writes ``skip_reason`` and never re-invokes the
            extension for this fingerprint until the user explicitly resets
            via ``musicorg reset-skip <fingerprint>`` (CONTRACT-1).
    """

    fingerprint_sha256: str
    status: UpgradeStatus
    new_path: Optional[str] = None
    quarantine_path: Optional[str] = None
    new_codec: Optional[str] = None
    new_bits: int = 0
    new_rate_hz: int = 0
    new_size_bytes: int = 0
    elapsed_sec: float = 0.0
    error_detail: str = ""
    permanent: bool = False


@dataclass
class PreflightResult:
    """Health-check record returned by :meth:`UpgradeExtension.preflight`.

    The core calls ``preflight()`` once at the start of an upgrade run,
    before iterating candidates. A non-healthy result aborts the run
    cleanly with a non-zero exit code (CONTRACT-2 orchestrator).

    Attributes:
        healthy: True iff the extension is ready to process candidates
            (binaries present, wrapper reachable, credentials valid, etc.).
        details: Human-readable diagnostic string. Required when
            ``healthy`` is False so the operator can see why.
        extension_name: Echoes the extension's :attr:`UpgradeExtension.name`
            for log/UI display.
        protocol_version: The contract version this extension speaks. The
            core rejects values not in :data:`SUPPORTED_PROTOCOL_VERSIONS`
            (CONTRACT-3).
    """

    healthy: bool
    details: str = ""
    extension_name: str = ""
    protocol_version: str = ""


@runtime_checkable
class UpgradeExtension(Protocol):
    """The contract every upgrade extension implements.

    gamdl is one implementation; future extensions (Qobuz, Tidal, local
    FLAC import) implement the same shape. ``@runtime_checkable`` lets the
    core verify shape at discovery time via ``isinstance(ext, UpgradeExtension)``
    when loading entry points from ``[project.entry-points."musicorg.upgrade"]``.

    Lifecycle (CONTRACT-2):
        1. Core resolves the extension by name from entry points.
        2. Core calls :meth:`preflight` once. If unhealthy, the run aborts.
        3. For each :class:`UpgradeCandidate`, the core calls
           :meth:`supports`. If False, the candidate is skipped.
        4. If supported, the core calls :meth:`upgrade` and writes the
           returned :class:`UpgradeResult` to ``04_upgrade_log.csv``.
        5. :meth:`cleanup` is called in a ``finally`` block — always,
           even on ``KeyboardInterrupt`` or extension crash.

    Attributes:
        name: Short identifier used to match entry points (e.g. ``"gamdl"``).
        protocol_version: Contract version string (e.g. ``"1.0"``). Must
            appear in :data:`SUPPORTED_PROTOCOL_VERSIONS` or the core
            refuses to dispatch (CONTRACT-3).
    """

    name: str
    protocol_version: str

    def preflight(self) -> PreflightResult:
        """Check that the extension can process candidates right now.

        Called once at the start of an upgrade run. Implementations should
        verify external dependencies (binaries, wrapper service, network,
        credentials) and return promptly. Must not raise on expected
        unhealthy states — return ``PreflightResult(healthy=False, details=...)``
        instead.
        """
        ...

    def supports(self, candidate: UpgradeCandidate) -> bool:
        """Return True iff this extension can attempt to upgrade ``candidate``.

        Used for cheap pre-filtering before the expensive :meth:`upgrade`
        call. For example, a Qobuz extension would return False when no
        Qobuz URL is present; a gamdl extension would return False when
        ``candidate.apple_music_url`` is empty or ``skip_reason`` is set.
        """
        ...

    def upgrade(self, candidate: UpgradeCandidate) -> UpgradeResult:
        """Attempt to upgrade one candidate to lossless.

        This is the per-track work unit. Must be safe to call concurrently
        across distinct fingerprints if the extension declares concurrency
        support (future contract extension). Must not raise on expected
        failure modes (region blocks, transient network errors, lossy-only
        availability) — encode them as :data:`UpgradeStatus` values instead.
        """
        ...

    def cleanup(self) -> None:
        """Release all resources held by the extension.

        Called in a ``finally`` block by the core orchestrator — always
        runs, even on ``KeyboardInterrupt`` or unexpected exception. Must
        be idempotent and must not raise; implementations should swallow
        their own teardown errors after logging them.
        """
        ...
