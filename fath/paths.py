"""Where everything lives, and how the settings root is found.

The settings root is configurable at install time but cannot be stored in the
config it points at, so it needs an external pointer. Resolution order, most
specific first:

  1. --home <path>      explicit, wins over everything (tests, two roots at once)
  2. $FATH_HOME         the natural override for a one-off
  3. the pointer file   %LOCALAPPDATA%/fathom-helper/home.path
  4. the default        %LOCALAPPDATA%/fathom-helper/

The pointer file, not the environment variable, is the load-bearing mechanism.
A Scheduled Task inherits the *logon* environment, so a FATH_HOME exported in a
shell -- or even set with `setx` and no logoff -- never reaches it. A scheduled
run and an interactive run resolving different roots would sync into two places
and neither would look wrong.

Resolution is cached per process, and failure here is loud rather than soft: a
pointer file naming a path that cannot be created is reported with the offending
path, because every downstream failure would otherwise be baffling.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "fathom-helper"
POINTER_FILE_NAME = "home.path"

_override: Path | None = None
_cached: Path | None = None


class PathsError(RuntimeError):
    """A settings root that cannot be used, phrased for a user."""


def is_windows() -> bool:
    return sys.platform == "win32"


def default_base() -> Path:
    """The fixed location, which is also where the pointer file lives.

    On Windows this is %LOCALAPPDATA%: machine-local and non-roaming, which is
    right for a cache of meeting content and a SQLite file. %APPDATA% roams, and
    a roaming profile is the last place a 100 MB database should go. Elsewhere,
    follow XDG.
    """
    if is_windows():
        raw = os.environ.get("LOCALAPPDATA")
        if raw:
            return Path(raw) / APP_DIR_NAME
        return Path.home() / "AppData" / "Local" / APP_DIR_NAME
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / APP_DIR_NAME


def pointer_path() -> Path:
    return default_base() / POINTER_FILE_NAME


def set_override(path: str | os.PathLike[str] | None) -> None:
    """Apply --home for the life of this process."""
    global _override, _cached
    _override = Path(path).expanduser() if path else None
    _cached = None


def read_pointer() -> Path | None:
    try:
        raw = pointer_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(raw).expanduser() if raw else None


def resolution() -> tuple[Path, str]:
    """The settings root and which of the four rules produced it.

    `fath doctor` and the web UI both show the source: a value that is right
    for the wrong reason is the one that bites later.
    """
    if _override is not None:
        return _override, "--home"
    from_env = os.environ.get("FATH_HOME", "").strip()
    if from_env:
        return Path(from_env).expanduser(), "$FATH_HOME"
    from_file = read_pointer()
    if from_file is not None:
        return from_file, f"pointer file ({pointer_path()})"
    return default_base(), "default"


def fath_home() -> Path:
    """The settings root, created if missing. Cached per process."""
    global _cached
    if _cached is not None:
        return _cached
    root, source = resolution()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PathsError(f"cannot use the settings root from {source}:\n  {root}\n  {exc}") from exc
    _cached = root
    return root


def config_path() -> Path:
    return fath_home() / "config.json"


def local_config_path() -> Path:
    return fath_home() / "local.json"


def state_dir() -> Path:
    path = fath_home() / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = fath_home() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return state_dir() / "fathom.db"


def token_path() -> Path:
    return fath_home() / "token"


def repo_root() -> Path:
    """The clone this file lives in -- where .env sits."""
    return Path(__file__).resolve().parent.parent


def env_file() -> Path:
    return repo_root() / ".env"


def default_output_root() -> Path:
    """Where meeting folders go when `outputRoot` is unset.

    Documents rather than the settings root: meeting content is something you
    browse and back up, not application state.
    """
    if is_windows():
        profile = os.environ.get("USERPROFILE")
        base = Path(profile) if profile else Path.home()
        return base / "Documents" / "Fathom"
    return Path.home() / "Fathom"
