"""Logging that makes a failed unattended run diagnosable.

The bar this has to clear is a real incident. A sync died with

    sqlite3.IntegrityError: UNIQUE constraint failed: meetings.folder

and that was the entire signal: no meeting id, no folder name, no indication of
which of several hundred rows was involved, and nothing about what the run had
been doing when it happened. Diagnosing it needed a database dump.

So three things:

  * a rotating file, always, even when the console is silenced -- a scheduled
    run has nowhere to print, and Task Scheduler's last-result column is a
    number,
  * one line per meeting at DEBUG, so the log says where a run got to,
  * a run header naming the version, the settings root, the output root and the
    options, because "it worked yesterday" is usually a changed path.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from fath import __version__, paths

LOG_NAME = "fath.log"
MAX_BYTES = 1_000_000
BACKUPS = 3

CONSOLE_FORMAT = "%(levelname).1s %(name)s: %(message)s"
FILE_FORMAT = "%(asctime)s %(levelname).1s %(name)s: %(message)s"


def log_path() -> Path | None:
    try:
        return paths.logs_dir() / LOG_NAME
    except (OSError, paths.PathsError):
        return None


def configure(level: str = "INFO", quiet: bool = False) -> Path | None:
    """Set up console and file logging. Returns the log path, or None.

    Never raises. An unwritable settings root must not stop a sync that would
    otherwise work: losing the log is a nuisance, refusing to run is a fault.
    """
    handlers: list[logging.Handler] = []
    path = log_path()

    if path is not None:
        try:
            handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter(FILE_FORMAT))
            handlers.append(handler)
        except OSError:
            path = None

    if path is None:
        handlers.append(logging.NullHandler())

    if not quiet:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
        handlers.append(console)

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=handlers,
        force=True,
    )
    return path


def log_run_header(logger: logging.Logger, action: str, **detail: object) -> None:
    """Record what this invocation is, before it can fail.

    Written at INFO even in a quiet run. Most "it stopped working" reports turn
    out to be a changed path or a different settings root, and this is the line
    that answers that without asking.
    """
    root, source = paths.resolution()
    logger.info("=" * 60)
    logger.info("fath %s | %s | python %s", __version__, action, sys.version.split()[0])
    logger.info("settings root : %s (from %s)", root, source)
    logger.info("pid           : %s", os.getpid())
    for key, value in detail.items():
        logger.info("%-14s: %s", key, value)
