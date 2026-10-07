"""`fath ui` -- the terminal dashboard."""

from __future__ import annotations

import sys

HELP = """usage: fath ui

A terminal dashboard: status, meetings, transcript search, a naming preview and
the settings, with a live log while a sync runs.

Needs Textual:  python -m pip install -r requirements.txt
"""


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(HELP)
        return 0
    try:
        from fath.ui.app import main as run_app
    except ImportError as exc:
        # One optional command missing a dependency must read as a missing
        # dependency, not as a broken install.
        print(f"fath ui: needs Textual ({exc})", file=sys.stderr)
        print("  install it with: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    return run_app(argv)
