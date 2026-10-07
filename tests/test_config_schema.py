"""Settings storage and the schema both UIs render from."""

from __future__ import annotations

import json
from pathlib import Path

from fath import config, paths, registry, schema

# -- storage ----------------------------------------------------------------- #


def test_defaults_apply_with_no_file_at_all() -> None:
    conf = config.shared()
    assert conf.get_int("naming.maxAttendees") == 5
    assert conf.source_of("naming.maxAttendees") == "default"


def test_saving_writes_only_the_changed_key(isolated_home: Path) -> None:
    """A config.json full of defaults cannot be told apart from a deliberate choice."""
    conf = config.shared()
    conf.set("naming.maxAttendees", 3)
    stored = json.loads((isolated_home / "config.json").read_text(encoding="utf-8"))
    assert stored == {"naming": {"maxAttendees": 3}}
    assert conf.get("naming.overflowSuffix") == "-and-{n}-more"


def test_the_local_overlay_wins_and_is_reported(isolated_home: Path) -> None:
    conf = config.shared()
    conf.set("web.port", 8899)
    (isolated_home / "local.json").write_text(json.dumps({"web": {"port": 9100}}), encoding="utf-8")
    conf.reload()
    assert conf.get_int("web.port") == 9100
    assert conf.source_of("web.port") == "local.json"


def test_underscore_keys_in_the_overlay_are_comments(isolated_home: Path) -> None:
    (isolated_home / "local.json").write_text(
        json.dumps({"_note": "why this machine differs", "web": {"port": 9100}}), encoding="utf-8"
    )
    conf = config.shared()
    conf.reload()
    assert conf.get_int("web.port") == 9100
    assert conf.get("_note") is None


def test_a_corrupt_config_falls_back_rather_than_crashing(isolated_home: Path) -> None:
    """Every setting silently reverting is bad; not starting at all is worse."""
    (isolated_home / "config.json").write_text("{ not json", encoding="utf-8")
    conf = config.Config()
    assert conf.get_int("naming.maxAttendees") == 5


def test_a_setting_saved_before_a_key_existed_still_works(isolated_home: Path) -> None:
    (isolated_home / "config.json").write_text(
        json.dumps({"naming": {"maxAttendees": 2}}), encoding="utf-8"
    )
    conf = config.Config()
    assert conf.get_int("naming.maxAttendees") == 2
    assert conf.get("naming.collisionSuffix") == "-{recording_id}"


def test_a_concurrent_write_does_not_lose_the_other_key(isolated_home: Path) -> None:
    """The web UI and a CLI run can both be live."""
    config.set_many({"web.port": 9000})
    config.set_many({"naming.maxAttendees": 4})
    stored = json.loads((isolated_home / "config.json").read_text(encoding="utf-8"))
    assert stored["web"]["port"] == 9000
    assert stored["naming"]["maxAttendees"] == 4


def test_typed_getters_tolerate_junk(isolated_home: Path) -> None:
    (isolated_home / "config.json").write_text(
        json.dumps({"web": {"port": "not a number"}, "files": {"media": "yes"}}), encoding="utf-8"
    )
    conf = config.Config()
    assert conf.get_int("web.port", 8899) == 8899
    assert conf.get_bool("files.media") is True


def test_the_output_root_follows_the_profile_when_unset() -> None:
    assert config.shared().output_root() == paths.default_output_root()


# -- the schema -------------------------------------------------------------- #


def test_every_declared_setting_has_a_default() -> None:
    """A setting with no default renders as blank in both UIs."""
    missing = [
        s.key
        for s in schema.SETTINGS
        if s.default() is None and s.kind not in (schema.KIND_TEXT, schema.KIND_PATH)
    ]
    assert missing == []


def test_every_setting_is_reachable_from_defaults() -> None:
    for setting in schema.SETTINGS:
        assert config.shared().get(setting.key) is not None or setting.kind in (
            schema.KIND_TEXT,
            schema.KIND_PATH,
            schema.KIND_LIST,
        ), setting.key


def test_validation_reports_every_problem_not_just_the_first() -> None:
    """A form that reports one error at a time takes five submits to fix five."""
    errors = schema.validate_many(
        {"naming.maxAttendees": 99, "naming.dateSource": "nope", "web.port": 10}
    )
    assert set(errors) == {"naming.maxAttendees", "naming.dateSource", "web.port"}


def test_validation_messages_are_for_a_person() -> None:
    errors = schema.validate_many({"naming.dateSource": "nope"})
    assert errors["naming.dateSource"].startswith("must be one of: recording_start_time")


def test_an_unknown_key_is_rejected() -> None:
    """The settings endpoint must not be a way to write arbitrary config."""
    assert schema.validate_many({"nosuch.key": 1}) == {"nosuch.key": "not a known setting"}


def test_form_strings_are_coerced_to_real_types() -> None:
    """A checkbox posts "true" and a number field posts "5"."""
    coerced = schema.coerce_many(
        {"files.media": "true", "naming.maxAttendees": "7", "api.minIntervalSec": "1.5"}
    )
    assert coerced == {
        "files.media": True,
        "naming.maxAttendees": 7,
        "api.minIntervalSec": 1.5,
    }


def test_coercion_drops_unknown_keys() -> None:
    assert schema.coerce_many({"nosuch.key": 1}) == {}


def test_describe_reports_the_winning_layer(isolated_home: Path) -> None:
    conf = config.shared()
    assert schema.describe("web.port", conf)["source"] == "default"
    conf.set("web.port", 9001)
    described = schema.describe("web.port", conf)
    assert described["source"] == "config.json"
    assert described["value"] == 9001
    assert described["default"] == 8899


def test_the_snapshot_is_the_read_model_the_uis_consume() -> None:
    rows = schema.snapshot()
    assert len(rows) == len(schema.SETTINGS)
    assert {r["group"] for r in rows} == set(schema.GROUPS)
    for row in rows:
        assert {"key", "label", "kind", "value", "default", "source", "note"} <= set(row)


def test_settings_notes_explain_the_expensive_choice() -> None:
    """The media checkbox is the one that can cost hours; it must say so."""
    note = schema.BY_KEY["files.media"].note.lower()
    assert "150 mb" in note and "10 requests per minute" in note


# -- the command table ------------------------------------------------------- #


def test_the_command_table_parses() -> None:
    assert "sync" in registry.names()
    assert registry.get("sync").summary


def test_an_unsupported_platform_is_stated_rather_than_missing() -> None:
    """`schedule` on POSIX must say so, not report 'command not found'."""
    assert registry.get("schedule").supported_on(is_windows=True)
    assert not registry.get("schedule").supported_on(is_windows=False)


def test_a_malformed_row_is_skipped_not_fatal() -> None:
    """A broken table must not take out `fath doctor`, which explains it."""
    parsed = registry.parse("good posix win a summary\nbroken row\n# comment\n")
    assert [c.name for c in parsed] == ["good"]
