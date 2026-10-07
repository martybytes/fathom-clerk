"""Fictional naming fixtures preserving known failure conditions.

Names, titles, dates, and identifiers are invented. See docs/naming.md for the
seven technical defects these tests guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fath import config, naming
from fath.naming import NamingRules, build_folder_name
from tests.conftest import make_meeting

# -- canonical examples from the naming rules -------------------------------- #


def test_matches_the_specified_example_one(rules: NamingRules) -> None:
    meeting = make_meeting(
        "Weekly Demo Review", ["Blaire Example", "Jules Example"], started="2024-04-19T14:00:00Z"
    )
    assert build_folder_name(meeting, rules) == "2024-04-19_weekly-demo-review_blaire-jules"


def test_matches_the_specified_example_two(rules: NamingRules) -> None:
    """ "Jules & Marley 1:1" -> "1-on-1".

    Two separate fixes meet here: "1:1" is replaced before punctuation is
    stripped (otherwise it becomes "1-1"), and the attendee names are removed
    from the title, leaving an orphaned "and" from the ampersand that the edge
    stopword trim then has to clear.
    """
    meeting = make_meeting(
        "Jules & Marley 1:1", ["Marley Example", "Jules Example"], started="2024-04-21T15:00:00Z"
    )
    assert build_folder_name(meeting, rules) == "2024-04-21_1-on-1_jules-marley"


# -- the charset rule -------------------------------------------------------- #


@pytest.mark.parametrize(
    "title",
    [
        "AB | CD: Change Management  / Risk Management Discussion",
        "Jules/Marley R&D Updates",
        "L10 Cadence - Example Traders/Sample Solutions",
        "quotes \"and\" 'apostrophes'",
        "emoji \U0001f600 and \u2014 dashes",
        "Caf\u00e9 R\u00e9sum\u00e9 Planung",
        "tabs\tand\nnewlines",
        "back`ticks $(and) ;semicolons| &pipes",
        "!!! ??? ***",
        "",
    ],
)
def test_output_is_always_shell_safe(title: str, rules: NamingRules) -> None:
    name = build_folder_name(make_meeting(title, ["Remy Example"]), rules)
    assert naming.SAFE_NAME.match(name), name
    assert " " not in name
    assert name == name.lower()


def test_pipe_colon_and_slash_title(rules: NamingRules) -> None:
    meeting = make_meeting(
        "AB | CD: Change Management  / Risk Management Discussion",
        ["Sam Example", "Nathan Example"],
    )
    assert build_folder_name(meeting, rules) == (
        "2024-04-24_ab-cd-change-management-risk-management-discussion_nathan-sam"
    )


def test_recorder_is_stripped_from_the_title_even_when_not_an_invitee(
    rules: NamingRules,
) -> None:
    """ "Jules/Marley R&D Updates" is recorded by Jules, who is not invited.

    Building the strip set from the invitee list alone leaves "jules" in the
    title, duplicating what the attendee segment already says.
    """
    meeting = make_meeting("Jules/Marley R&D Updates", ["Marley Example"])
    assert build_folder_name(meeting, rules) == "2024-04-24_r-and-d-updates_marley"


# -- attendees --------------------------------------------------------------- #


def test_comma_inside_an_attendee_name(rules: NamingRules) -> None:
    """ "Casey Example, Ph.D." must give "casey", not a stray " ph.d." field."""
    meeting = make_meeting(
        "R&D Example and Priorities",
        ["Casey Example, Ph.D.", "Noor Example", "Conference Room 238"],
    )
    assert build_folder_name(meeting, rules).endswith("_casey-noor")


def test_conference_rooms_are_not_people(rules: NamingRules) -> None:
    meeting = make_meeting("Parts Database", ["Conference Room 238", "Nathan Sample"])
    assert naming.attendee_names(meeting, rules) == ["Nathan Sample"]


def test_a_meeting_of_only_rooms_has_no_attendee_segment(rules: NamingRules) -> None:
    meeting = make_meeting("Parts Database", ["Conference Room 238", "Conference Room 239"])
    name = build_folder_name(meeting, rules)
    # The recorder still counts, so the segment survives; what must not happen is
    # a room appearing as though it were a colleague.
    assert "conference" not in name
    assert not name.endswith("_")


def test_no_invitees_leaves_no_trailing_separator(rules: NamingRules) -> None:
    """The attendee segment is built from invitees, not from the recorder.

    An empty segment must collapse along with its separator rather than leaving
    a name ending in "_".
    """
    meeting = make_meeting("Impromptu Microsoft Teams Meeting", [])
    name = build_folder_name(meeting, rules)
    assert name == "2024-04-24_impromptu-microsoft-teams-meeting"
    assert not name.endswith("_")


def test_duplicate_first_names_collapse(rules: NamingRules) -> None:
    """Erin Example and Erin Sample are both "erin"."""
    meeting = make_meeting("Example Consulting", ["Erin Example", "Erin Sample", "Remy Example"])
    assert build_folder_name(meeting, rules).endswith("_erin-remy")


def test_email_addresses_as_names_use_the_local_part(rules: NamingRules) -> None:
    """parker@example-labs.example must be "parker", not "parker-example-labs-example".

    Two of these in one meeting overflowed MAX_PATH.
    """
    meeting = make_meeting(
        "Example Labs Sync",
        ["parker@example-labs.example", "lena@example-labs.example", "first.last@acme.example"],
    )
    name = build_folder_name(meeting, rules)
    assert "example-labs-example" not in name
    for expected in ("parker", "lena", "first"):
        assert expected in name


def test_overflow_summarises_the_rest(rules: NamingRules) -> None:
    meeting = make_meeting("Big Meeting", [f"Person{i} Surname" for i in range(11)])
    name = build_folder_name(meeting, rules)
    assert "-and-" in name and "-more" in name


# -- length and paths -------------------------------------------------------- #


def test_the_cap_is_derived_from_the_output_root(rules: NamingRules) -> None:
    """The name appears twice in the deepest path, so the budget is halved.

    A flat 120-character cap produced a 292-character path under an ordinary
    Documents folder, which Windows rejects outright.
    """
    shallow = naming.max_name_len(Path("C:/F"), rules)
    deep = naming.max_name_len(Path("C:/a/very/deep/nested/output/folder/for/meetings"), rules)
    assert deep < shallow


def test_a_long_title_still_fits_inside_max_path(rules: NamingRules) -> None:
    out_root = Path("C:/Users/someone/Documents/Fathom")
    meeting = make_meeting("A " + "very " * 60 + "long title", ["Remy Example"])
    name = build_folder_name(meeting, rules, out_root)
    longest = out_root / name / f"{name}{naming.LONGEST_FILE_SUFFIX}"
    assert len(str(longest)) < naming.WINDOWS_MAX_PATH


def test_long_attendee_names_shed_after_the_title_bottoms_out(
    rules: NamingRules,
) -> None:
    """The cap must be able to drop attendees, not only shorten the title.

    With the title already at its floor, a meeting whose invitees are long email
    addresses could not get any shorter and overflowed regardless.
    """
    out_root = Path("C:/Users/someone/Documents/Fathom")
    meeting = make_meeting(
        "Quarterly",
        [f"averyverylongfirstname{i}@some-long-domain-name.example.com" for i in range(8)],
    )
    name = build_folder_name(meeting, rules, out_root)
    longest = out_root / name / f"{name}{naming.LONGEST_FILE_SUFFIX}"
    assert len(str(longest)) < naming.WINDOWS_MAX_PATH


def test_truncation_lands_on_a_word_boundary(rules: NamingRules) -> None:
    """A severed word reads as a different word, not as an abbreviation.

    "commercial-r-and-d-pipeline-meet" and "...sample-sol" are the real ones.
    """
    out_root = Path("C:/some/quite/deep/output/root/for/fathom/meetings/here")
    meeting = make_meeting(
        "Commercial Research And Development Pipeline Meeting Planning Session",
        ["Remy Example"],
    )
    name = build_folder_name(meeting, rules, out_root)
    title = name.split("_")[1]
    assert not title.endswith("-")
    # Every surviving word must be a whole word from the original.
    words = set(naming.slug(meeting["title"]).split("-"))
    assert set(title.split("-")) <= words


def test_collision_suffix_is_inside_the_cap_not_appended_after(
    rules: NamingRules,
) -> None:
    """Appending the suffix afterwards can overflow MAX_PATH.

    The folder is created -- short enough on its own -- and the write inside it
    then fails with a bare FileNotFoundError naming a path that looked fine.
    """
    out_root = Path("C:/Users/someone/Documents/Fathom")
    meeting = make_meeting("A " + "very " * 60 + "long title", ["Remy Example"])
    suffix = naming.collision_suffix_for(rules, 123456789)
    name = build_folder_name(meeting, rules, out_root, suffix)
    longest = out_root / name / f"{name}{naming.LONGEST_FILE_SUFFIX}"
    assert name.endswith(suffix)
    assert len(str(longest)) < naming.WINDOWS_MAX_PATH


# -- dates ------------------------------------------------------------------- #


def test_utc_is_converted_to_local_before_formatting(rules: NamingRules) -> None:
    """A post-midnight-UTC meeting must file under the local day.

    Formatting the UTC value directly files an evening Central meeting under the
    following date, which nobody notices until they go looking for it.
    """
    meeting = make_meeting("Late Call", ["Remy Example"], started="2024-04-22T02:30:00Z")
    local_day = naming.meeting_datetime(meeting, rules).strftime("%Y-%m-%d")
    assert build_folder_name(meeting, rules).startswith(local_day)


def test_utc_mode_is_available_when_asked_for(rules: NamingRules) -> None:
    utc_rules = naming.dataclass_replace(rules, {"timezone_mode": "utc"})
    meeting = make_meeting("Late Call", ["Remy Example"], started="2024-04-22T02:30:00Z")
    assert build_folder_name(meeting, utc_rules).startswith("2024-04-22")


def test_a_malformed_timestamp_falls_through_rather_than_raising(
    rules: NamingRules,
) -> None:
    meeting = make_meeting("Odd", ["Remy Example"], started="not-a-date")
    meeting["scheduled_start_time"] = "2024-04-20T15:00:00Z"
    assert build_folder_name(meeting, rules).startswith("2024-04-20")


# -- reserved names ---------------------------------------------------------- #


def test_windows_device_names_are_escaped(rules: NamingRules) -> None:
    """A folder called `con` cannot be created on Windows at all."""
    meeting = make_meeting("CON", [], recorder="")
    name = build_folder_name(meeting, rules)
    assert name.startswith("x-")
    # Not "_", which would corrupt the segment split that makes names parseable.
    assert not name.startswith("_")


# -- settings drive the rules ------------------------------------------------ #


def test_excluding_the_recorder_shortens_every_name(rules: NamingRules) -> None:
    meeting = make_meeting("Weekly Demo Review", ["Blaire Example", "Jules Example"])
    without = naming.dataclass_replace(rules, {"exclude_recorder": True})
    assert build_folder_name(meeting, without, None).endswith("_blaire")


def test_overrides_preview_without_saving() -> None:
    """The web UI previews a naming change against unsaved settings."""
    conf = config.shared()
    proposed = NamingRules.from_overrides({"naming.maxAttendees": 1}, conf)
    meeting = make_meeting("Big Meeting", ["Anna A", "Bob B", "Cara C"])
    assert "-and-" in build_folder_name(meeting, proposed)
    # ...and the saved settings are untouched by the preview.
    assert conf.get_int("naming.maxAttendees") == 5
