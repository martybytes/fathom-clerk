"""Settings: code-level defaults, a JSON file, and a machine-local overlay.

Reads fath_home()/config.json deep-merged with fath_home()/local.json, local
winning key by key and "_"-prefixed keys skipped so a file can carry a comment.
Values are read by dotted path against DEFAULTS below, so a config.json written
before any given key existed still yields today's behaviour rather than a
KeyError.

ONE WRITER. Every persisted setting goes through set() here and nowhere else.
Two stores that each render a valid file from their own copy is how terminal-
stack lost all five of its Claude TTS hooks in a single day; the web UI and the
CLI therefore call this, rather than each writing JSON they believe is correct.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fath import paths

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    # Empty means paths.default_output_root(), resolved at use rather than baked
    # in: the default must follow the user profile, not the machine it was first
    # written on.
    "outputRoot": "",
    "naming": {
        "folderTemplate": "{date}_{title}_{attendees}",
        "dateFormat": "%Y-%m-%d",
        "dateSource": "recording_start_time",
        "titleSource": "title",
        "timezone": "local",
        "lowercase": True,
        "segmentSeparator": "_",
        "wordSeparator": "-",
        "stripAttendeeNamesFromTitle": True,
        "titleReplacements": {"1:1": "1 on 1", "&": " and ", "+": " plus ", "@": " at "},
        "edgeStopwords": "a an and at by for from in of on or the to via vs with plus",
        "maxAttendees": 5,
        "overflowSuffix": "-and-{n}-more",
        "excludeAttendeePatterns": [
            "Conference Room*",
            "Zoom Room*",
            "*Meeting Room*",
            "Account Management",
        ],
        "excludeRecorder": False,
        "dedupeFirstNames": True,
        "maxFolderNameLen": 120,
        "minFolderNameLen": 40,
        "collisionSuffix": "-{recording_id}",
    },
    "files": {
        "prefixWithFolderName": True,
        "transcript": True,
        "summary": True,
        "actionItems": True,
        "meetingJson": True,
        "highlights": False,
        "media": False,
    },
    "sync": {
        "backfillSince": "",
        "recordedBy": [],
        "overwriteExisting": False,
        "reimportBeforeSync": True,
        "overlapMinutes": 60,
    },
    "api": {
        "baseUrl": "https://api.fathom.ai/external/v1",
        "timeoutSec": 30,
        "minIntervalSec": 1.0,
        "reserveRequests": 2,
        "maxRetries": 5,
        "maxBackoffSec": 120,
    },
    "web": {"port": 8899, "openBrowser": True},
    "log": {"level": "INFO", "retainDays": 30},
}


def _deep_merge(base: Any, overlay: Any) -> Any:
    """Overlay wins key by key; "_"-prefixed keys in the overlay are skipped."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = dict(base)
        for key, value in overlay.items():
            if isinstance(key, str) and key.startswith("_"):
                continue
            out[key] = _deep_merge(base.get(key), value) if key in base else value
        return out
    return overlay if overlay is not None else base


def _read_json_dict(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write via a temp file in the same directory, then replace.

    A half-written config.json is worse than an unchanged one: it fails to parse,
    every setting silently reverts to its default, and nothing says so. Same
    directory so os.replace stays atomic -- a cross-device move is not.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


@contextlib.contextmanager
def file_lock(path: Path, wait_sec: float = 5.0) -> Iterator[None]:
    """A crude cross-process mutex around a settings write.

    Proceeds after wait_sec rather than refusing: a settings save that silently
    does nothing is worse than two writers racing on a file that is rewritten
    whole anyway.
    """
    lock = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + wait_sec
    acquired = False
    while True:
        try:
            handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(handle)
            acquired = True
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                log.warning("proceeding without the lock on %s", lock)
                break
            time.sleep(0.05)
        except OSError:
            break
    try:
        yield
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                lock.unlink()


def _walk(data: Any, dotted: str) -> Any:
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _assign(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def set_many(values: dict[str, Any]) -> None:
    """Persist several settings in one read-modify-write.

    Reads the file again inside the lock rather than trusting an in-memory copy:
    the web UI and a CLI run can both be live, and writing a stale whole document
    would drop whatever the other one just saved.
    """
    path = paths.config_path()
    with file_lock(path):
        data = _read_json_dict(path)
        for dotted, value in values.items():
            _assign(data, dotted, value)
        write_json_atomic(path, data)
    log.info("saved %d setting(s)", len(values))


class Config:
    """Thread-safe merged settings with dotted-path access and hot reload."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        merged: Any = {}
        for path in (paths.config_path(), paths.local_config_path()):
            if not path.is_file():
                continue
            merged = _deep_merge(merged, _read_json_dict(path))
        with self._lock:
            self._data = merged if isinstance(merged, dict) else {}

    def get(self, dotted: str, default: Any = None) -> Any:
        """Dotted lookup, falling back to DEFAULTS and then to `default`."""
        with self._lock:
            found = _walk(self._data, dotted)
        if found is not None:
            return found
        built_in = _walk(DEFAULTS, dotted)
        return built_in if built_in is not None else default

    def get_int(self, dotted: str, default: int = 0) -> int:
        try:
            return int(self.get(dotted, default))
        except (TypeError, ValueError):
            return default

    def get_float(self, dotted: str, default: float = 0.0) -> float:
        try:
            return float(self.get(dotted, default))
        except (TypeError, ValueError):
            return default

    def get_bool(self, dotted: str, default: bool = False) -> bool:
        value = self.get(dotted, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def get_list(self, dotted: str) -> list[Any]:
        value = self.get(dotted, [])
        return list(value) if isinstance(value, (list, tuple)) else []

    def get_dict(self, dotted: str) -> dict[str, Any]:
        value = self.get(dotted, {})
        return dict(value) if isinstance(value, dict) else {}

    def source_of(self, dotted: str) -> str:
        """Which layer won: local.json, config.json, or default."""
        if _walk(_read_json_dict(paths.local_config_path()), dotted) is not None:
            return "local.json"
        if _walk(_read_json_dict(paths.config_path()), dotted) is not None:
            return "config.json"
        return "default"

    def effective(self) -> dict[str, Any]:
        with self._lock:
            merged = _deep_merge(DEFAULTS, self._data)
        return merged if isinstance(merged, dict) else {}

    def output_root(self) -> Path:
        raw = str(self.get("outputRoot", "") or "").strip()
        return Path(raw).expanduser() if raw else paths.default_output_root()

    def set(self, dotted: str, value: Any) -> None:
        """The only writer. Persists to config.json and refreshes this instance."""
        set_many({dotted: value})
        self.reload()

    def set_many(self, values: dict[str, Any]) -> None:
        set_many(values)
        self.reload()


_shared: Config | None = None


def shared() -> Config:
    global _shared
    if _shared is None:
        _shared = Config()
    return _shared


def reset_shared() -> None:
    """Drop the process-wide instance. For tests and after --home changes."""
    global _shared
    _shared = None
