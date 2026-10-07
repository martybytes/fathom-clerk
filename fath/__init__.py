"""fathom-helper: pull Fathom meetings onto disk, one folder each."""

from __future__ import annotations

__all__ = ["__version__"]

# Not a release version. The tool updates by `git pull`, so the clone's HEAD is
# the only version that means anything.
__version__ = "0.1.0"
