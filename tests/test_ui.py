"""The terminal dashboard: the one-writer rule, and that it actually boots.

The first test here is the important one. Two components each writing a file
they believed they owned is how a settings file loses edits. A rule that is
only written down is a rule that drifts, so it is asserted instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("textual", reason="the terminal dashboard is an optional extra")

from fath import config
from fath.ui.app import FathApp

UI_DIR = Path(__file__).resolve().parent.parent / "fath" / "ui"

# Anything that puts bytes on disk where settings live. The UI may read these
# modules; it may not call the functions that write.
FORBIDDEN_CALLS = {
    "write_json_atomic",
    "set_many",
    "write_text",
    "write_bytes",
    "set_api_key",
    "mkdir",
}
# Reaching for a config path at all is the smell that precedes writing to one.
FORBIDDEN_ATTRIBUTES = {"config_path", "local_config_path", "pointer_path", "env_file"}


def ui_sources() -> list[Path]:
    return sorted(UI_DIR.glob("*.py"))


def test_there_is_something_to_check() -> None:
    """A vacuous assertion is worse than no assertion."""
    assert ui_sources(), f"no python files found under {UI_DIR}"


@pytest.mark.parametrize("source", ui_sources(), ids=lambda p: p.name)
def test_the_ui_never_writes_settings_itself(source: Path) -> None:
    """It must call config.set(), which is the one writer, and nothing else."""
    tree = ast.parse(source.read_text(encoding="utf-8"))

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name in FORBIDDEN_CALLS:
            offenders.append(f"{name}() at line {node.lineno}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            offenders.append(f"paths.{node.attr} at line {node.lineno}")

    assert not offenders, (
        f"{source.name} writes settings directly: {', '.join(offenders)}. "
        "Route it through config.set() instead -- see docs/decisions.md."
    )


def test_saving_goes_through_the_shared_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """_save must reach config.set(), not a JSON dump of its own."""
    app = FathApp()
    seen: list[tuple[str, object]] = []
    monkeypatch.setattr(type(app.cfg), "set", lambda _self, key, value: seen.append((key, value)))
    monkeypatch.setattr(app, "notify", lambda *a, **k: None)
    monkeypatch.setattr(app, "refresh_preview", lambda: None)
    monkeypatch.setattr(app, "refresh_overview", lambda: None)

    app._save("naming.maxAttendees", "4")
    assert seen == [("naming.maxAttendees", 4)]


def test_an_invalid_setting_is_refused_rather_than_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FathApp()
    seen: list[object] = []
    complaints: list[str] = []
    monkeypatch.setattr(type(app.cfg), "set", lambda *a: seen.append(a))
    monkeypatch.setattr(app, "notify", lambda msg, **k: complaints.append(str(msg)))

    app._save("naming.maxAttendees", "99")
    assert seen == []
    assert complaints and "at most 20" in complaints[0]


def test_bad_json_for_a_list_setting_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FathApp()
    seen: list[object] = []
    complaints: list[str] = []
    monkeypatch.setattr(type(app.cfg), "set", lambda *a: seen.append(a))
    monkeypatch.setattr(app, "notify", lambda msg, **k: complaints.append(str(msg)))

    app._save("naming.excludeAttendeePatterns", "not json")
    assert seen == []
    assert complaints and "JSON" in complaints[0]


@pytest.mark.asyncio
async def test_the_settings_screen_renders_every_non_advanced_setting() -> None:
    """A setting added to schema.py must appear here without extra work.

    Inside run_test() because Textual widgets cannot be constructed without a
    running app -- there is no active-app context to attach them to.
    """
    from fath import schema
    from fath.ui.app import key_to_id

    expected = [s for s in schema.SETTINGS if schema.ADVANCED not in s.flags]
    assert expected, "nothing to render"

    app = FathApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        for setting in expected:
            assert app.query(f"#{key_to_id(setting.key)}"), f"{setting.key} not rendered"


@pytest.mark.asyncio
async def test_the_app_boots_and_every_tab_mounts() -> None:
    """A broken pane otherwise only shows itself when someone opens that tab."""
    from textual.widgets import TabbedContent

    app = FathApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#overview")
        assert app.query_one("#table")

        tabs = app.query_one(TabbedContent)
        for tab in ("meetings", "search", "naming", "settings", "home"):
            tabs.active = tab
            await pilot.pause()
        assert tabs.active == "home"


@pytest.mark.asyncio
async def test_the_overview_reports_the_key_state() -> None:
    app = FathApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        rendered = str(app.query_one("#overview").render())
        assert "meetings" in rendered
        assert "api key" in rendered


def test_config_is_the_shared_instance_not_a_private_one() -> None:
    """Two Config objects would mean the UI could show a stale value."""
    assert FathApp().cfg is config.shared()
