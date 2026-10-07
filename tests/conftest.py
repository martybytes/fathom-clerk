"""Shared fixtures.

This file exists for one reason: no test may be able to touch the real machine.

That is not hypothetical. An earlier version of `isolated_home` overrode
`paths.set_override()` but not `default_base()`, and `pointer_path()` derives
from `default_base()`. So `fath init --home <tmp>` running inside a test wrote a
real pointer file to the user's actual %LOCALAPPDATA%, aimed at a pytest temp
directory. Every subsequent `fath` command then resolved its settings root
to a throwaway test folder.

The fixture below closes that hole, and test_isolation.py asserts it stays
closed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fath import config, db, naming, paths


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every path-resolving helper at a throwaway directory.

    Both halves matter. `set_override` covers fath_home(); redirecting
    LOCALAPPDATA/XDG_STATE_HOME covers default_base(), and therefore
    pointer_path(), which is the one that escaped.
    """
    home = tmp_path / "home"
    home.mkdir()
    fake_local = tmp_path / "localappdata"
    fake_local.mkdir()

    monkeypatch.delenv("FATH_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local))
    monkeypatch.setenv("XDG_STATE_HOME", str(fake_local))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    # Path.home() ignores USERPROFILE on some platforms, so pin it too.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "profile"))

    paths.set_override(home)
    config.reset_shared()
    db.reset_shared()
    yield home
    paths.set_override(None)
    config.reset_shared()
    db.reset_shared()


@pytest.fixture(autouse=True)
def no_writes_to_the_real_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Never let a test write the repository's own .env, which holds a live key."""
    monkeypatch.setattr(paths, "env_file", lambda: tmp_path / "sandbox" / ".env")


@pytest.fixture
def rules() -> naming.NamingRules:
    return naming.NamingRules.from_config(config.shared())


def make_meeting(
    title: str,
    invitees: list[str] | None = None,
    recorder: str = "Jules Example",
    started: str = "2024-04-24T14:00:00Z",
    recording_id: int = 1,
    **extra: Any,
) -> dict[str, Any]:
    """A fictional meeting payload shaped like the API's; never copy account data here."""
    meeting: dict[str, Any] = {
        "title": title,
        "meeting_title": title,
        "recording_id": recording_id,
        "url": f"https://fathom.video/calls/{recording_id}",
        "share_url": f"https://fathom.video/share/{recording_id}",
        "recording_start_time": started,
        "created_at": started,
        "recorded_by": {"name": recorder},
        "calendar_invitees": [{"name": n} for n in (invitees or [])],
        "transcript": [],
        "default_summary": {},
        "action_items": [],
    }
    meeting.update(extra)
    return meeting


def real_localappdata() -> Path | None:
    """The genuine %LOCALAPPDATA%, captured before any fixture redirects it."""
    raw = _REAL_LOCALAPPDATA
    return Path(raw) if raw else None


# Captured at import, before any monkeypatching can run.
_REAL_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
