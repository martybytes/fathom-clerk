"""The API key: where it is read from, and how it is written back.

The splice tests matter most. The real .env holds a webhook secret and hand-typed
comments next to the key, and rewriting that file from a parsed dict would
silently discard both.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fath import keystore

REAL_SHAPE = (
    "# Fathom Personal API key\n"
    "# API Docs at https://developers.fathom.ai/\n"
    "FATHOM-PERSONAL-API-KEY=old-key-value\n"
    "FATHOM-PERSON-WEBHOOK-SECRET=whsec-do-not-touch\n"
)


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(REAL_SHAPE, encoding="utf-8")
    return path


def test_dashes_and_underscores_are_the_same_key() -> None:
    """`export FATHOM-PERSONAL-API-KEY=x` is a shell syntax error.

    The dashed spelling can only ever live in a file, so both must resolve to one
    entry or the file and the environment disagree about the same key.
    """
    assert keystore.normalise("FATHOM-PERSONAL-API-KEY") == "FATHOM_PERSONAL_API_KEY"
    parsed = keystore.parse_env("FATHOM-PERSONAL-API-KEY=abc\n")
    assert parsed["FATHOM_PERSONAL_API_KEY"] == "abc"


def test_parse_ignores_comments_blanks_and_quotes() -> None:
    parsed = keystore.parse_env(
        "# a comment\n\nA=\"quoted\"\nB='single'\nC=plain\nnot-an-assignment\n"
    )
    assert parsed == {"A": "quoted", "B": "single", "C": "plain"}


def test_the_splice_preserves_everything_else(env_file: Path) -> None:
    keystore.set_api_key("NEW1234567890abcdef", env_file)
    after = env_file.read_text(encoding="utf-8")

    assert "whsec-do-not-touch" in after, "the webhook secret must survive"
    assert after.count("#") == 2, "both comments must survive"
    assert "old-key-value" not in after
    assert "FATHOM-PERSONAL-API-KEY=NEW1234567890abcdef" in after
    assert len(after.strip().splitlines()) == 4, "no lines added or lost"


def test_the_splice_keeps_the_spelling_already_in_the_file(env_file: Path) -> None:
    """Rewriting the name as well as the value changes the user's convention."""
    keystore.set_api_key("abcdef1234567890", env_file)
    assert "FATHOM-PERSONAL-API-KEY=" in env_file.read_text(encoding="utf-8")
    assert "FATHOM_PERSONAL_API_KEY=" not in env_file.read_text(encoding="utf-8")


def test_writing_to_a_file_with_no_key_line_appends(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# only a comment\nSOMETHING_ELSE=1\n", encoding="utf-8")
    keystore.set_api_key("abcdef1234567890", path)
    after = path.read_text(encoding="utf-8")
    assert "SOMETHING_ELSE=1" in after
    assert after.rstrip().endswith("FATHOM-PERSONAL-API-KEY=abcdef1234567890")


def test_writing_to_a_missing_file_creates_it(tmp_path: Path) -> None:
    path = tmp_path / "nested" / ".env"
    assert keystore.set_api_key("abcdef1234567890", path)
    assert "abcdef1234567890" in path.read_text(encoding="utf-8")


def test_clearing_empties_the_value_but_keeps_the_line(env_file: Path) -> None:
    keystore.set_api_key("", env_file)
    after = env_file.read_text(encoding="utf-8")
    assert "FATHOM-PERSONAL-API-KEY=\n" in after
    assert "whsec-do-not-touch" in after


def test_the_environment_wins_over_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".env"
    path.write_text("FATHOM-PERSONAL-API-KEY=from-file\n", encoding="utf-8")
    monkeypatch.setattr(keystore.paths, "env_file", lambda: path)

    value, source = keystore.resolve()
    assert (value, source) == ("from-file", ".env (FATHOM_PERSONAL_API_KEY)")

    monkeypatch.setenv("FATHOM_PERSONAL_API_KEY", "from-env")
    value, source = keystore.resolve()
    assert (value, source) == ("from-env", "$FATHOM_PERSONAL_API_KEY")


def test_an_unreadable_env_is_no_value_rather_than_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller must be able to prompt, not crash, when the file is missing."""
    monkeypatch.setattr(keystore.paths, "env_file", lambda: tmp_path / "nope" / ".env")
    monkeypatch.delenv("FATHOM_PERSONAL_API_KEY", raising=False)
    assert keystore.resolve() == ("", "")


def test_describe_never_leaks_the_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / ".env"
    path.write_text("FATHOM-PERSONAL-API-KEY=supersecretvalue9999\n", encoding="utf-8")
    monkeypatch.setattr(keystore.paths, "env_file", lambda: path)
    monkeypatch.delenv("FATHOM_PERSONAL_API_KEY", raising=False)

    described = keystore.describe()
    assert described["set"] is True
    assert described["tail"] == "9999"
    assert "supersecretvalue" not in repr(described)


def test_a_short_key_shows_no_tail_at_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Four characters of a seven-character secret is most of it."""
    path = tmp_path / ".env"
    path.write_text("FATHOM-PERSONAL-API-KEY=short\n", encoding="utf-8")
    monkeypatch.setattr(keystore.paths, "env_file", lambda: path)
    monkeypatch.delenv("FATHOM_PERSONAL_API_KEY", raising=False)
    assert keystore.describe()["tail"] == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("abcdefghij1234567890", True),
        ("with.dots_and-dashes-0123", True),
        ("short", False),
        ("has spaces in it here", False),
        ("has$dollar#signs!!!!!!!", False),
        ("", False),
    ],
)
def test_shape_check(value: str, expected: bool) -> None:
    assert keystore.looks_like_api_key(value) is expected
