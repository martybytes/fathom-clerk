"""The suite must not be able to touch the real machine.

This file exists because it already happened. `fath init --home <tmp>` running
under test wrote a pointer file to the user's genuine %LOCALAPPDATA%, because
`pointer_path()` derives from `default_base()` and only `fath_home()` was being
redirected. Every real `fath` command afterwards resolved into a pytest temp
directory holding a fake meeting, and the next sync died with

    sqlite3.IntegrityError: UNIQUE constraint failed: meetings.folder

The bug was not the constraint. The bug was a test writing outside its sandbox,
and it stayed invisible because nothing asserted otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fath import keystore, paths
from fath.commands import init
from tests.conftest import real_localappdata


def test_the_settings_root_is_inside_the_sandbox(isolated_home: Path, tmp_path: Path) -> None:
    assert paths.fath_home() == isolated_home
    assert tmp_path in paths.fath_home().parents or paths.fath_home() == isolated_home


def test_the_pointer_path_is_inside_the_sandbox(tmp_path: Path) -> None:
    """The one that escaped. pointer_path() comes from default_base()."""
    pointer = paths.pointer_path()
    assert tmp_path in pointer.parents, f"pointer_path escaped the sandbox: {pointer}"

    # Not "the real LOCALAPPDATA is not an ancestor": pytest's tmp_path lives
    # under %LOCALAPPDATA%\Temp, so it always is. The thing that must never
    # happen is writing the one exact file the real installation reads.
    genuine = real_localappdata()
    if genuine is not None:
        assert pointer != genuine / "fathom-helper" / "home.path"


def test_the_default_output_root_is_inside_the_sandbox(tmp_path: Path) -> None:
    """Otherwise a sync test could write meeting folders into real Documents."""
    assert tmp_path in paths.default_output_root().parents


def test_the_env_file_is_not_the_repository_one() -> None:
    """The repo .env holds a live API key and a webhook secret."""
    assert paths.env_file() != paths.repo_root() / ".env"


def test_init_writes_its_pointer_inside_the_sandbox(tmp_path: Path) -> None:
    """The exact call that leaked."""
    target = tmp_path / "custom-root"
    assert init.main(["--home", str(target), "--output-root", str(tmp_path / "o"), "--yes"]) == 0

    pointer = paths.pointer_path()
    assert pointer.is_file()
    assert tmp_path in pointer.parents

    genuine = real_localappdata()
    if genuine is not None:
        escaped = genuine / "fathom-helper" / "home.path"
        # Not just "we wrote elsewhere" -- assert the real file was not created
        # by this test. If a developer has one legitimately, this still holds,
        # because the test's target path is under tmp_path and cannot match.
        assert pointer != escaped


def test_writing_a_key_cannot_touch_the_repository_env(tmp_path: Path) -> None:
    repo_env = paths.repo_root() / ".env"
    before = repo_env.read_bytes() if repo_env.is_file() else None

    keystore.set_api_key("abcdef1234567890sandbox")

    after = repo_env.read_bytes() if repo_env.is_file() else None
    assert after == before, "a test rewrote the repository's .env"


@pytest.mark.parametrize("variable", ["LOCALAPPDATA", "XDG_STATE_HOME", "USERPROFILE"])
def test_the_environment_is_redirected(variable: str, tmp_path: Path) -> None:
    value = os.environ.get(variable, "")
    assert value, f"{variable} should be set by the fixture"
    assert str(tmp_path) in value, f"{variable} still points at the real profile"
