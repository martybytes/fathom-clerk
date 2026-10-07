"""What gets written into a meeting folder, and what the hash covers."""

from __future__ import annotations

from pathlib import Path

import pytest

from fath import writers
from fath.naming import NamingRules
from fath.writers import FileSelection
from tests.conftest import make_meeting

FULL = {
    "transcript": [
        {"speaker": {"display_name": "Blaire Example"}, "timestamp": "00:00:11", "text": "Hello."},
        {"speaker": {"display_name": "Jules"}, "timestamp": "00:00:20", "text": "  "},
    ],
    "default_summary": {"template_name": "Enhanced", "markdown_formatted": "## Notes\n\n- one\n"},
    "action_items": [
        {
            "description": "Send the deck",
            "completed": False,
            "recording_timestamp": "00:01:00",
            "recording_playback_url": "https://fathom.video/calls/1?t=60",
            "assignee": {"name": "Jules Example"},
        }
    ],
    "highlights": [{"type": "Decision", "summary": "ship it", "start_time": 42}],
}


@pytest.fixture
def meeting() -> dict:
    return make_meeting("Weekly Demo Review", ["Blaire Example", "Jules Example"], **FULL)


def test_front_matter_makes_a_stray_file_self_describing(meeting: dict, rules: NamingRules) -> None:
    """A transcript copied elsewhere must still say what meeting it came from."""
    text = writers.render_transcript(meeting, rules)
    assert text.startswith("---\n")
    for expected in ("Weekly Demo Review", "recording_id: 1", "Blaire Example", "share_url:"):
        assert expected in text


def test_transcript_lines_carry_timestamp_and_speaker(meeting: dict, rules: NamingRules) -> None:
    text = writers.render_transcript(meeting, rules)
    assert "**[00:00:11] Blaire Example:** Hello." in text
    assert "00:00:20" not in text, "a whitespace-only line is not a line"


def test_an_empty_transcript_says_so_rather_than_being_blank(rules: NamingRules) -> None:
    text = writers.render_transcript(make_meeting("Quiet", []), rules)
    assert "No transcript available" in text


def test_summary_and_action_items_render(meeting: dict, rules: NamingRules) -> None:
    summary = writers.render_summary(meeting, rules)
    assert "# Summary (Enhanced)" in summary and "## Notes" in summary

    actions = writers.render_action_items(meeting, rules)
    assert "- [ ] **Jules Example** - Send the deck" in actions
    assert "(https://fathom.video/calls/1?t=60)" in actions

    highlights = writers.render_highlights(meeting, rules)
    assert "**Decision** (42s) - ship it" in highlights


def test_missing_pieces_do_not_crash_the_renderers(rules: NamingRules) -> None:
    bare = make_meeting("Bare", [])
    for render in (
        writers.render_transcript,
        writers.render_summary,
        writers.render_action_items,
        writers.render_highlights,
    ):
        assert render(bare, rules)


def test_the_selection_decides_which_files_exist(meeting: dict, rules: NamingRules) -> None:
    only_transcript = FileSelection(
        transcript=True, summary=False, action_items=False, meeting_json=False
    )
    names = set(writers.plan_files(meeting, "folder", rules, only_transcript))
    assert names == {"folder_transcript.md"}


def test_filenames_can_drop_the_folder_prefix(meeting: dict, rules: NamingRules) -> None:
    plain = FileSelection(prefix_with_folder=False)
    names = writers.plan_files(meeting, "folder", rules, plain)
    assert "transcript.md" in names and "folder_transcript.md" not in names


def test_the_hash_ignores_fields_that_change_on_every_call(meeting: dict) -> None:
    """Fathom returns a fresh signed share_url each time.

    Hashing the whole payload would mark every meeting changed on every run and
    rewrite unchanged folders nightly for nothing.
    """
    first = writers.content_hash(meeting)
    meeting["share_url"] = "https://fathom.video/share/completely-different"
    meeting["crm_matches"] = {"error": "no CRM connected"}
    assert writers.content_hash(meeting) == first


def test_the_hash_notices_real_changes(meeting: dict) -> None:
    before = writers.content_hash(meeting)
    meeting["title"] = "Renamed"
    assert writers.content_hash(meeting) != before


def test_writing_produces_the_folder_and_a_stamp(
    tmp_path: Path, meeting: dict, rules: NamingRules
) -> None:
    folder = tmp_path / "2024-04-19_weekly-demo-review_blaire-jules"
    written = writers.write_meeting(folder, meeting, folder.name, rules, FileSelection())
    assert len(written) == 4
    assert (folder / writers.STAMP_NAME).is_file()


def test_the_stamp_is_written_even_without_meeting_json(
    tmp_path: Path, meeting: dict, rules: NamingRules
) -> None:
    """Without it a rebuild cannot tell which recording a folder belongs to."""
    folder = tmp_path / "f"
    writers.write_meeting(folder, meeting, "f", rules, FileSelection(meeting_json=False))
    assert not list(folder.glob("*meeting.json"))
    assert (folder / writers.STAMP_NAME).is_file()


def test_a_path_that_is_too_long_says_why(
    tmp_path: Path, meeting: dict, rules: NamingRules
) -> None:
    """Windows raises a bare FileNotFoundError that reads as a missing directory."""
    folder = tmp_path / "f"
    with pytest.raises(OSError, match="path too long"):
        writers.write_meeting(folder, meeting, "f", rules, FileSelection(), max_path=40)


def test_selection_overrides_come_from_the_dotted_setting_names() -> None:
    """The web form posts setting keys, not a second vocabulary."""
    selection = FileSelection.from_overrides({"files.media": True, "files.summary": False})
    assert selection.media is True
    assert selection.summary is False
    assert selection.transcript is True, "unmentioned keys keep the saved value"
