"""Extension subsystem for musicorg.

Upgrade extensions implement the UpgradeExtension protocol and ship as
separate packages (e.g. musicorg-gamdl) registered via entry points.
"""

from .protocol import (
    SUPPORTED_PROTOCOL_VERSIONS,
    PreflightResult,
    UpgradeCandidate,
    UpgradeExtension,
    UpgradeResult,
    UpgradeStatus,
)

__all__ = [
    "SUPPORTED_PROTOCOL_VERSIONS",
    "PreflightResult",
    "UpgradeCandidate",
    "UpgradeExtension",
    "UpgradeResult",
    "UpgradeStatus",
]
