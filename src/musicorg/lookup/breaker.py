"""Filesystem-backed circuit breaker for unstable external tiers.

Shazamio is the primary motivation: it talks to Shazam's internal API and can
break at any time. A persistent on-disk marker survives across runs so we
don't keep hammering a dead service. The breaker is intentionally simple —
in-process counter + marker file — to be debuggable from the shell.
"""

from __future__ import annotations

import datetime
from pathlib import Path


class CircuitBreaker:
    """Counts consecutive failures; trips by writing a marker file.

    Once a marker exists, ``is_tripped()`` returns its path and callers should
    skip the tier until the operator deletes the marker (or calls ``reset``).
    """

    def __init__(self, name: str, threshold: int, marker_dir: Path) -> None:
        self.name = name
        self.threshold = max(1, int(threshold))
        self.marker_dir = Path(marker_dir)
        self._failures = 0

    def _marker_path(self, today: str | None = None) -> Path:
        day = today or datetime.date.today().isoformat()
        return self.marker_dir / f"{self.name}_UNAVAILABLE.{day}.txt"

    def is_tripped(self) -> Path | None:
        """Return the marker file path if any exists, else ``None``."""
        if not self.marker_dir.exists():
            return None
        for p in self.marker_dir.glob(f"{self.name}_UNAVAILABLE.*.txt"):
            return p
        return None

    def record_success(self) -> None:
        """Reset the in-process consecutive-failure counter."""
        self._failures = 0

    def record_failure(self, reason: str) -> bool:
        """Increment counter; trip + write marker if threshold reached.

        Returns ``True`` iff this call tripped the breaker (i.e. marker was
        just written). Subsequent failures past the threshold also return
        ``True`` so callers can continue to short-circuit.
        """
        self._failures += 1
        if self._failures >= self.threshold:
            self.marker_dir.mkdir(parents=True, exist_ok=True)
            path = self._marker_path()
            today = datetime.date.today().isoformat()
            path.write_text(
                f"{self.name} failed on {today}.\n\n"
                f"Reason recorded:\n{reason}\n\n"
                f"This tier will be skipped on subsequent runs until this file\n"
                f"is deleted. Before retrying:\n"
                f"  1. Update the client library if applicable.\n"
                f"  2. Check the upstream service's status.\n"
                f"  3. If still broken, consider an alternative tier.\n"
                f"\nAfter the service is working again:\n"
                f"  rm {path}\n"
            )
            return True
        return False

    def reset(self) -> bool:
        """Delete any existing marker. Returns ``True`` if one was removed."""
        self._failures = 0
        removed = False
        if self.marker_dir.exists():
            for p in self.marker_dir.glob(f"{self.name}_UNAVAILABLE.*.txt"):
                try:
                    p.unlink()
                    removed = True
                except FileNotFoundError:
                    pass
        return removed
