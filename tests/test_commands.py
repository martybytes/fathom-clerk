"""The CLI: dispatch, help, exit codes, and each command's behaviour.

Exit codes are load-bearing. The Scheduled Task reports the last result, and the
PowerShell shim passes it through, so `fath sync` returning 0 after a failure
would make an unattended sync look healthy while doing nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fath import cli, config, db, keystore, logging_setup, naming, paths
from fath.commands import config as config_cmd
from fath.commands import doctor, init, key, search, status


def test_help_lists_every_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    for name in ("sync", "status", "search", "config", "key", "doctor"):
        assert name in out
    assert "settings root:" in out


def test_no_arguments_shows_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == cli.EXIT_OK
    assert "usage: fath" in capsys.readouterr().out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == cli.EXIT_OK
    assert capsys.readouterr().out.startswith("fath ")


def test_an_unknown_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["nosuchthing"]) == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "unknown command" in err
    assert "try one of" in err, "an error must say what the options are"


def test_home_flag_is_stripped_before_the_subcommand_sees_it(tmp_path: Path) -> None:
    """--home is global; a subcommand parsing it as its own flag would reject it."""
    target = tmp_path / "elsewhere"
    assert cli.main(["--home", str(target), "status"]) == cli.EXIT_OK
    assert target.is_dir()


def test_home_flag_also_accepts_the_equals_form(tmp_path: Path) -> None:
    target = tmp_path / "equals"
    assert cli.main([f"--home={target}", "status"]) == cli.EXIT_OK
    assert target.is_dir()


# -- status ------------------------------------------------------------------ #


def test_status_reports_the_essentials(capsys: pytest.CaptureFixture[str]) -> None:
    assert status.main([]) == 0
    out = capsys.readouterr().out
    for label in ("settings root", "output folder", "api key", "database", "meetings"):
        assert label in out


def test_status_says_never_when_nothing_has_run(capsys: pytest.CaptureFixture[str]) -> None:
    status.main([])
    assert "last run      : never" in capsys.readouterr().out


# -- config ------------------------------------------------------------------ #


def test_config_lists_grouped_settings(capsys: pytest.CaptureFixture[str]) -> None:
    assert config_cmd.main([]) == 0
    out = capsys.readouterr().out
    assert "Naming" in out and "Downloads" in out
    assert "built-in default" in out


def test_config_json_is_the_read_model(capsys: pytest.CaptureFixture[str]) -> None:
    assert config_cmd.main(["--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert any(r["key"] == "naming.maxAttendees" for r in rows)


def test_config_shows_one_setting_with_its_note(capsys: pytest.CaptureFixture[str]) -> None:
    assert config_cmd.main(["files.media"]) == 0
    out = capsys.readouterr().out
    assert "150 MB" in out, "the expensive option must explain itself"


def test_config_sets_a_value(capsys: pytest.CaptureFixture[str]) -> None:
    assert config_cmd.main(["naming.maxAttendees", "3"]) == 0
    assert config.shared().get_int("naming.maxAttendees") == 3


def test_config_refuses_an_invalid_value(capsys: pytest.CaptureFixture[str]) -> None:
    assert config_cmd.main(["naming.maxAttendees", "99"]) == 2
    assert "at most 20" in capsys.readouterr().err
    assert config.shared().get_int("naming.maxAttendees") == 5, "nothing may be saved"


def test_config_refuses_an_unknown_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert config_cmd.main(["nosuch.key", "1"]) == 2
    assert "no setting called" in capsys.readouterr().err


def test_config_accepts_json_for_a_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert config_cmd.main(["naming.excludeAttendeePatterns", '["Room*"]']) == 0
    assert config.shared().get_list("naming.excludeAttendeePatterns") == ["Room*"]


def test_config_unset_restores_the_default(capsys: pytest.CaptureFixture[str]) -> None:
    config_cmd.main(["naming.maxAttendees", "2"])
    assert config_cmd.main(["--unset", "naming.maxAttendees"]) == 0
    assert config.shared().get_int("naming.maxAttendees") == 5


# -- key --------------------------------------------------------------------- #


def test_key_reports_a_missing_key_as_a_failure(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FATHOM_PERSONAL_API_KEY", raising=False)
    monkeypatch.setattr(keystore.paths, "env_file", lambda: tmp_path / "absent" / ".env")
    assert key.main([]) == 1
    out = capsys.readouterr().out
    assert "no API key found" in out
    assert "fath key --set" in out, "an error must say how to fix it"


def test_key_never_prints_the_value(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text("FATHOM-PERSONAL-API-KEY=supersecretvalue1234\n", encoding="utf-8")
    monkeypatch.setattr(keystore.paths, "env_file", lambda: env)
    monkeypatch.delenv("FATHOM_PERSONAL_API_KEY", raising=False)

    assert key.main([]) == 0
    out = capsys.readouterr().out
    assert "supersecretvalue" not in out
    assert "1234" in out, "the last four are enough to tell two keys apart"


# -- search ------------------------------------------------------------------ #


def test_search_with_no_terms_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert search.main([]) == 2


def test_search_reports_no_matches_plainly(capsys: pytest.CaptureFixture[str]) -> None:
    assert search.main(["nothinglikethis"]) == 0
    assert "no matches" in capsys.readouterr().out


def test_search_finds_an_indexed_phrase(capsys: pytest.CaptureFixture[str]) -> None:
    database = db.shared()
    database.upsert_meeting(
        db.MeetingRow(recording_id=1, title="Weekly", folder="f", started_at="2024-04-19T10:00:00Z")
    )
    database.index_transcript(
        1,
        [
            {
                "speaker": {"display_name": "Blaire Example"},
                "timestamp": "00:00:11",
                "text": "the commercial pipeline is the blocker",
            }
        ],
    )
    assert search.main(["pipeline"]) == 0
    out = capsys.readouterr().out
    assert "Blaire Example" in out and "00:00:11" in out


# -- doctor ------------------------------------------------------------------ #


def test_doctor_flags_a_missing_key(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FATHOM_PERSONAL_API_KEY", raising=False)
    monkeypatch.setattr(keystore.paths, "env_file", lambda: tmp_path / "absent" / ".env")
    assert doctor.main([]) == 1
    out = capsys.readouterr().out
    assert "no Fathom API key" in out
    assert "fath key --set" in out


def test_doctor_json_is_assertable_by_check_id(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every finding has a stable id, so tests never match on prose."""
    monkeypatch.delenv("FATHOM_PERSONAL_API_KEY", raising=False)
    monkeypatch.setattr(keystore.paths, "env_file", lambda: tmp_path / "absent" / ".env")
    doctor.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    by_id = {c["check"]: c for c in payload["checks"]}
    assert by_id["key.present"]["status"] == doctor.FAIL
    assert by_id["home.source"]["status"] == doctor.OK
    assert payload["ok"] is False


def test_doctor_passes_with_a_key_present(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FATHOM_PERSONAL_API_KEY", "abcdefghij1234567890")
    assert doctor.main([]) == 0
    assert "all good" in capsys.readouterr().out


def test_doctor_warns_when_the_output_folder_is_too_deep(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deep output root silently shortens every folder name."""
    monkeypatch.setenv("FATHOM_PERSONAL_API_KEY", "abcdefghij1234567890")
    deep = tmp_path / ("nested" + "/very-long-directory-name" * 8)
    config.shared().set("outputRoot", str(deep))
    doctor.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    by_id = {c["check"]: c for c in payload["checks"]}
    assert by_id["output.depth"]["status"] in (doctor.NOTE, doctor.FAIL)


# -- init -------------------------------------------------------------------- #


def test_init_creates_the_root_and_takes_defaults(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    home = tmp_path / "chosen-home"
    out = tmp_path / "chosen-output"
    assert init.main(["--home", str(home), "--output-root", str(out), "--yes"]) == 0
    assert home.is_dir() and out.is_dir()
    assert "settings root" in capsys.readouterr().out


def test_init_writes_a_pointer_only_for_a_non_default_root(tmp_path: Path) -> None:
    """A default install must leave nothing extra behind."""
    home = tmp_path / "custom"
    init.main(["--home", str(home), "--output-root", str(tmp_path / "o"), "--yes"])
    assert paths.pointer_path().read_text(encoding="utf-8").strip() == str(home.resolve())


def test_init_refuses_to_silently_orphan_an_existing_root(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Moving data is the user's call; the command prints how and stops."""
    first = tmp_path / "first"
    init.main(["--home", str(first), "--output-root", str(tmp_path / "o"), "--yes"])
    capsys.readouterr()

    second = tmp_path / "second"
    assert init.main(["--home", str(second), "--force", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "nothing has been moved" in out
    assert "robocopy" in out


def test_init_is_idempotent(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    home = tmp_path / "home"
    init.main(["--home", str(home), "--output-root", str(tmp_path / "o"), "--yes"])
    capsys.readouterr()
    assert init.main([]) == 0
    assert "already set up" in capsys.readouterr().out


def test_init_adopts_folders_it_is_pointed_at(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The whole point: existing meetings cost no API requests to pick up."""
    existing = tmp_path / "existing"
    folder = existing / "2024-04-19_weekly-demo-review_blaire-jules"
    folder.mkdir(parents=True)
    (folder / "meeting.json").write_text(
        json.dumps(
            {
                "recording_id": 4242,
                "title": "Weekly Demo Review",
                "recording_start_time": "2024-04-19T14:00:00Z",
                "recorded_by": {"name": "Jules Example"},
                "calendar_invitees": [{"name": "Blaire Example"}],
            }
        ),
        encoding="utf-8",
    )
    assert (
        init.main(
            [
                "--home",
                str(tmp_path / "h"),
                "--output-root",
                str(tmp_path / "o"),
                "--import",
                str(existing),
                "--yes",
            ]
        )
        == 0
    )
    assert "adopted" in capsys.readouterr().out
    assert db.shared().get_meeting(4242) is not None


def test_init_does_not_advertise_poc_out(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A leftover research folder must not be mentioned on first-run setup."""
    leftover = paths.repo_root() / "poc" / "out"
    leftover.mkdir(parents=True, exist_ok=True)
    probe = leftover / "_init_probe_folder"
    probe.mkdir(exist_ok=True)
    try:
        assert (
            init.main(
                ["--home", str(tmp_path / "h"), "--output-root", str(tmp_path / "o"), "--yes"]
            )
            == 0
        )
        out = capsys.readouterr().out
        assert "adopt them without re-downloading" not in out
        assert str(leftover) not in out
    finally:
        if probe.is_dir():
            probe.rmdir()


# -- schedule ---------------------------------------------------------------- #


def test_schedule_is_refused_with_a_reason_off_windows(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """'not available' must say what to use instead, not just decline."""
    from fath.commands import schedule

    monkeypatch.setattr(schedule.paths, "is_windows", lambda: False)
    assert schedule.main(["--status"]) == 2
    err = capsys.readouterr().err
    assert "Windows-only" in err
    assert "cron" in err


def test_schedule_maps_flags_onto_the_script(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI vocabulary and the PowerShell switch names are deliberately different."""
    from fath.commands import schedule

    seen: dict[str, list[str]] = {}

    class Completed:
        returncode = 0

    def fake_run(command, check=False):  # type: ignore[no-untyped-def]
        seen["command"] = list(command)
        return Completed()

    monkeypatch.setattr(schedule.paths, "is_windows", lambda: True)
    monkeypatch.setattr(schedule, "_pwsh", lambda: "pwsh")
    monkeypatch.setattr(schedule.subprocess, "run", fake_run)

    assert schedule.main(["--remove"]) == 0
    assert "-Uninstall" in seen["command"]

    assert schedule.main(["--every", "6", "--at", "05:30"]) == 0
    assert "-IntervalHours" in seen["command"] and "6" in seen["command"]
    assert "-StartTime" in seen["command"] and "05:30" in seen["command"]


def test_schedule_defaults_to_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running it bare must report, never install something unasked."""
    from fath.commands import schedule

    seen: dict[str, list[str]] = {}

    class Completed:
        returncode = 0

    monkeypatch.setattr(schedule.paths, "is_windows", lambda: True)
    monkeypatch.setattr(schedule, "_pwsh", lambda: "pwsh")
    monkeypatch.setattr(
        schedule.subprocess,
        "run",
        lambda command, check=False: (seen.update(command=list(command)), Completed())[1],
    )
    assert schedule.main([]) == 0
    assert "-Status" in seen["command"]


def test_schedule_rejects_an_unknown_flag(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from fath.commands import schedule

    monkeypatch.setattr(schedule.paths, "is_windows", lambda: True)
    assert schedule.main(["--nonsense"]) == 2
    assert "unknown option" in capsys.readouterr().err


# -- logging ----------------------------------------------------------------- #


def test_quiet_still_writes_a_log_file(tmp_path: Path) -> None:
    """An unattended run has nowhere to print; the file is the only evidence."""
    import logging

    from fath import paths

    logging_setup.configure("INFO", quiet=True)
    logging.getLogger("fath.test").info("hello from a scheduled run")
    logging.shutdown()
    assert "hello from a scheduled run" in (paths.logs_dir() / "fath.log").read_text(
        encoding="utf-8"
    )


def test_doctor_flags_folders_too_long_for_the_current_output_root(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moving meetings to a deeper folder can put existing names over the limit.

    Copying a synced tree from a short path into a OneDrive path did this to 122
    existing folders and pushed files past MAX_PATH. fath never writes such a
    path itself, so nothing would otherwise have said a word.
    """
    monkeypatch.setenv("FATHOM_PERSONAL_API_KEY", "abcdefghij1234567890")
    # Sized so the folder name is over the derived cap but the path is still
    # creatable -- the first attempt at this fixture hit MAX_PATH itself.
    out = tmp_path / "deep" / ("nested" * 8) / ("more" * 6)
    out.mkdir(parents=True)
    cap = naming.max_name_len(out, naming.NamingRules.from_config(config.shared()))
    (out / ("x" * (cap + 20))).mkdir()
    config.shared().set("outputRoot", str(out))

    assert doctor.main(["--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    by_id = {c["check"]: c for c in payload["checks"]}
    assert by_id["output.lengths"]["status"] in (doctor.FAIL, doctor.NOTE)
    assert "exceed" in by_id["output.lengths"]["message"]
    assert "overwrite" in by_id["output.lengths"]["hint"]


def test_doctor_is_quiet_when_every_folder_fits(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FATHOM_PERSONAL_API_KEY", "abcdefghij1234567890")
    out = tmp_path / "Fathom"
    (out / "2024-04-19_weekly_blaire").mkdir(parents=True)
    config.shared().set("outputRoot", str(out))

    doctor.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    by_id = {c["check"]: c for c in payload["checks"]}
    assert by_id["output.lengths"]["status"] == doctor.OK
