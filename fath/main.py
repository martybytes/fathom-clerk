#!/usr/bin/env python3
"""Entry point. Run as a file, not as a module.

    python <clone>/fath/main.py <command> [args]

The shim in $PROFILE invokes it this way on purpose. `python -m fath` would need
the clone on PYTHONPATH, which means either exporting a variable from the shell
or shipping a wrapper -- and PYTHONPATH is inherited by every child process, so a
stale one would poison unrelated Python in the session. Putting the clone on
sys.path here, from __file__, keeps the effect local.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Output is ASCII by convention, but meeting titles and paths are not ours to
# control: a transcript under a non-Latin-1 name must not die with a
# UnicodeEncodeError on a legacy Windows console codepage. Replace rather than
# raise -- a mangled character beats no output at all.
for _stream in (sys.stdout, sys.stderr):
    # Redirected to something without reconfigure, or a closed handle.
    with contextlib.suppress(AttributeError, OSError):
        _stream.reconfigure(errors="replace")  # type: ignore[union-attr]

from fath.cli import main  # noqa: E402  (import must follow the sys.path fix)

if __name__ == "__main__":
    sys.exit(main())
