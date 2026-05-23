"""PyQt6 desktop UI for musicorg.

A multi-page window mirroring the wizard's three stages. All heavy work
runs on QThread workers via the same musicorg library calls the CLI uses —
nothing is duplicated.
"""
