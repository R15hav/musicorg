"""musicorg_cli — reference CLI consumer of the musicorg library.

This package is the canonical example of how to embed musicorg in a Python
application. It uses Typer for the command surface, Rich for output rendering,
and Textual for the interactive TUI screens. Every operation it performs goes
through musicorg's public API — there is no privileged access.

To run the CLI: install with ``pip install musicorg[cli]`` and use ``musicorg``.
To learn from the CLI as a code reference, start with ``musicorg_cli.main``.
"""

__version__ = "0.2.0"
