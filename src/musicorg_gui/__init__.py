"""musicorg_gui — desktop GUI for the musicorg library.

Distributed via GitHub Releases only (AppImage / .exe / .dmg). NOT
published to PyPI; the published ``musicorg`` wheel excludes this
package. See ``DISTRIBUTION.md`` and ``PUBLIC_API.md`` for the boundary.

The GUI is a consumer of the ``musicorg`` public API. It imports only
from ``musicorg.*`` re-exports — never from internal submodules.
"""

__version__ = "0.1.0-dev"


def main() -> int:
    """Console-script entry point — defers PySide6 import until called."""
    from .app import run

    return run()
