"""Example 04 — custom upgrade extension.

Implement the UpgradeExtension protocol to teach musicorg about a new
upgrade source. This example: a "local lossless archive" — if the user
has a pristine FLAC of a track somewhere on disk, use that instead of
downloading from Apple Music via gamdl.

The same shape works for Qobuz, Tidal, Bandcamp, soulseek, etc.

Key points:
  - No inheritance required. musicorg checks structural compatibility via
    isinstance(ext, UpgradeExtension) at runtime because UpgradeExtension is
    @runtime_checkable. You only need to implement the four methods + two attrs.
  - preflight() is called once before the batch loop; fail fast here.
  - supports() is a cheap pre-filter; the real work happens in upgrade().
  - cleanup() is called in a finally block — even on KeyboardInterrupt.
  - extensions never mutate UpgradeCandidate — it is a frozen dataclass.

Run:
    python 04_custom_extension.py
"""

from __future__ import annotations
from pathlib import Path

from musicorg import (
    PreflightResult,
    SUPPORTED_PROTOCOL_VERSIONS,
    UpgradeCandidate,
    UpgradeExtension,
    UpgradeResult,
)


class LocalLosslessArchive:
    """An UpgradeExtension that looks for matching FLACs in a local archive.

    The archive is assumed to be a flat or artist-organised folder of
    ``<artist> - <title>.flac`` files.  A real implementation would use
    fuzzy matching (e.g. rapidfuzz) and would walk subdirectories, but
    keeping it simple here illustrates the protocol shape without adding
    dependencies.
    """

    name = "local-flac-archive"
    protocol_version = "1.0"

    def __init__(self, archive_root: Path) -> None:
        self.archive_root = archive_root

    # ------------------------------------------------------------------
    # UpgradeExtension protocol
    # ------------------------------------------------------------------

    def preflight(self) -> PreflightResult:
        """Verify the archive directory exists before starting the batch loop."""
        if not self.archive_root.exists():
            return PreflightResult(
                healthy=False,
                details=f"archive root not found: {self.archive_root}",
                extension_name=self.name,
                protocol_version=self.protocol_version,
            )
        return PreflightResult(
            healthy=True,
            details=f"archive root ok: {self.archive_root}",
            extension_name=self.name,
            protocol_version=self.protocol_version,
        )

    def supports(self, candidate: UpgradeCandidate) -> bool:
        """Return True for any candidate — this is a broad source.

        supports() is the cheap pre-filter step; the real existence check
        lives in upgrade().  A Qobuz extension would return False here when
        no Qobuz URL is present; we accept everything and let upgrade() decide.
        """
        # Skip candidates the core has already marked as permanently done.
        return candidate.skip_reason is None

    def upgrade(self, candidate: UpgradeCandidate) -> UpgradeResult:
        """Look for a FLAC matching artist + title in the archive directory.

        The naming convention assumed here is ``<artist> - <title>.flac``.
        A production implementation would use a fuzzy-match index built at
        preflight() time rather than constructing a single expected path.
        """
        expected_name = f"{candidate.canonical_artist} - {candidate.canonical_title}.flac"
        target = self.archive_root / expected_name

        if not target.exists():
            # Not finding it now is non-permanent: a new FLAC may be added
            # to the archive later.  permanent=False keeps the row in the
            # upgradable list for future runs.
            return UpgradeResult(
                fingerprint_sha256=candidate.fingerprint_sha256,
                status="lossy_only_confirmed",
                permanent=False,
                error_detail=f"no match in archive for: {expected_name}",
            )

        # Found a match.  The core orchestrator handles the actual copy/move
        # based on the new_path we return — we just point at the source file.
        return UpgradeResult(
            fingerprint_sha256=candidate.fingerprint_sha256,
            status="upgraded",
            new_path=str(target),
            new_codec="flac",
            new_size_bytes=target.stat().st_size,
        )

    def cleanup(self) -> None:
        """Nothing to release — this extension is read-only against the archive."""
        pass


def main() -> int:
    ext = LocalLosslessArchive(archive_root=Path.home() / "lossless-archive")

    # Runtime protocol check — catches missing methods before the batch loop.
    assert isinstance(ext, UpgradeExtension), (
        f"{ext.__class__.__name__} does not implement the UpgradeExtension protocol"
    )
    assert ext.protocol_version in SUPPORTED_PROTOCOL_VERSIONS, (
        f"protocol version {ext.protocol_version!r} not in {SUPPORTED_PROTOCOL_VERSIONS}"
    )

    print(f"extension    : {ext.name}")
    print(f"protocol     : {ext.protocol_version}")
    result = ext.preflight()
    print(f"preflight    : healthy={result.healthy}  details={result.details!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
