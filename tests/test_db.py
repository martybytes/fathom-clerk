"""The database: resume, skip-unchanged, collision detection and search."""

from __future__ import annotations

from pathlib import Path

import pytest

from fath.db import Database, MeetingRow


@pytest.fixture
def database(tmp_path: Path) -> Database:
    return Database(tmp_path / "fathom.db")


def row(recording_id: int = 1, folder: str = "2024-04-19_a_b", **kwargs: object) -> MeetingRow:
    base = {
        "title": "Weekly Demo Review",
        "started_at": "2024-04-19T09:01:48-05:00",
        "attendees": ["Blaire Example", "Jules Example"],
        "content_hash": "sha256:abc",
    }
    base.update(kwargs)
    return MeetingRow(recording_id=recording_id, folder=folder, **base)  # type: ignore[arg-type]


def test_upsert_is_idempotent(database: Database) -> None:
    database.upsert_meeting(row())
    database.upsert_meeting(row())
    assert database.count_meetings() == 1


def test_upsert_updates_rather_than_duplicating(database: Database) -> None:
    database.upsert_meeting(row(title="Old"))
    database.upsert_meeting(row(title="New"))
    stored = database.get_meeting(1)
    assert stored is not None and stored.title == "New"
    assert database.count_meetings() == 1


def test_attendees_survive_the_json_round_trip(database: Database) -> None:
    database.upsert_meeting(row(attendees=["Casey Example, Ph.D.", "Noor Example"]))
    stored = database.get_meeting(1)
    assert stored is not None
    assert stored.attendees == ["Casey Example, Ph.D.", "Noor Example"]


def test_content_hash_drives_skip_unchanged(database: Database) -> None:
    database.upsert_meeting(row(content_hash="sha256:abc"))
    assert database.content_hash(1) == "sha256:abc"
    assert database.content_hash(999) == "", "an unknown meeting must not look unchanged"


def test_folder_owner_finds_collisions(database: Database) -> None:
    """Two identical recurring meetings on one day produce the same name.

    Without this lookup the second silently overwrites the first.
    """
    database.upsert_meeting(row(recording_id=1, folder="2024-05-21_l10_a-b"))
    assert database.folder_owner("2024-05-21_l10_a-b") == 1
    assert database.folder_owner("2024-05-21_something-else") is None


def test_an_empty_folder_name_is_not_a_collision(database: Database) -> None:
    """The unique index is partial; rows imported without a folder must not clash."""
    database.upsert_meeting(row(recording_id=1, folder=""))
    database.upsert_meeting(row(recording_id=2, folder=""))
    assert database.count_meetings() == 2


def test_listing_pages_and_sorts(database: Database) -> None:
    for i in range(1, 6):
        database.upsert_meeting(
            row(recording_id=i, folder=f"f{i}", started_at=f"2024-04-0{i}T10:00:00-05:00")
        )
    rows, total = database.list_meetings(limit=2)
    assert total == 5
    assert [r.folder for r in rows] == ["f5", "f4"]

    rows, _ = database.list_meetings(limit=2, offset=2)
    assert [r.folder for r in rows] == ["f3", "f2"]

    rows, _ = database.list_meetings(descending=False, limit=1)
    assert rows[0].folder == "f1"


def test_listing_filters_on_title_and_attendees(database: Database) -> None:
    database.upsert_meeting(row(recording_id=1, folder="f1", title="ExampleCo"))
    database.upsert_meeting(
        row(recording_id=2, folder="f2", title="Other", attendees=["Marley Example"])
    )
    assert database.list_meetings(query="exampleco")[1] == 1
    assert database.list_meetings(query="Marley")[1] == 1


def test_an_unknown_sort_column_cannot_become_sql(database: Database) -> None:
    """The order column is interpolated, so it is allow-listed."""
    database.upsert_meeting(row())
    _, total = database.list_meetings(order="started_at; DROP TABLE meetings--")
    assert total == 1
    assert database.count_meetings() == 1


def test_search_returns_snippets_with_speaker_and_timestamp(database: Database) -> None:
    database.upsert_meeting(row())
    database.index_transcript(
        1,
        [
            {
                "speaker": {"display_name": "Blaire Example"},
                "timestamp": "00:00:11",
                "text": "Let us review the commercial pipeline before Thursday.",
            },
            {"speaker": {"display_name": "Jules"}, "timestamp": "00:01:02", "text": ""},
        ],
    )
    hits = database.search("pipeline")
    assert len(hits) == 1
    assert hits[0]["speaker"] == "Blaire Example"
    assert hits[0]["timestamp"] == "00:00:11"
    assert "pipeline" in hits[0]["snippet"].lower()
    assert hits[0]["title"] == "Weekly Demo Review"


def test_reindexing_replaces_rather_than_duplicates(database: Database) -> None:
    database.upsert_meeting(row())
    for _ in range(3):
        database.index_transcript(
            1, [{"speaker": {"display_name": "A"}, "timestamp": "00:00:01", "text": "unique word"}]
        )
    assert len(database.search("unique")) == 1


def test_a_malformed_search_does_not_raise(database: Database) -> None:
    """An unbalanced quote is a user typing, not a server error."""
    assert database.search('unbalanced "quote') == []
    assert database.search("") == []


def test_runs_record_counts_and_close(database: Database) -> None:
    run_id = database.start_run({"media": False})
    assert database.active_run() is not None
    database.finish_run(run_id, "done", written=5, meetings_seen=7, requests_made=2)

    last = database.last_run()
    assert last is not None
    assert (last["status"], last["written"], last["requests_made"]) == ("done", 5, 2)
    assert database.active_run() is None


def test_events_replay_in_order_and_carry_data(database: Database) -> None:
    run_id = database.start_run({})
    database.add_event(run_id, "started", data={"total": 2})
    database.add_event(run_id, "wrote a-folder", level="info")
    database.add_event(run_id, "went wrong", level="error")

    events = database.events_since(run_id)
    assert [e["message"] for e in events] == ["started", "wrote a-folder", "went wrong"]
    assert events[0]["data"] == {"total": 2}
    assert events[2]["level"] == "error"

    # A reconnecting UI asks only for what it has not seen.
    later = database.events_since(run_id, after_id=events[0]["id"])
    assert len(later) == 2


def test_a_crashed_run_is_closed_rather_than_blocking_forever(database: Database) -> None:
    """A row left 'running' makes the UI report a sync in progress for good."""
    database.start_run({})
    database.start_run({})
    assert database.mark_stale_runs() == 2
    assert database.active_run() is None
    assert database.last_run()["status"] == "interrupted"


def test_latest_started_at_drives_the_incremental_window(database: Database) -> None:
    assert database.latest_started_at() == ""
    database.upsert_meeting(row(recording_id=1, folder="f1", started_at="2024-04-01T10:00:00Z"))
    database.upsert_meeting(row(recording_id=2, folder="f2", started_at="2024-04-19T10:00:00Z"))
    assert database.latest_started_at() == "2024-04-19T10:00:00Z"


def test_multi_word_search_falls_back_to_meeting_level(database: Database) -> None:
    """One row is one spoken line, so FTS5's implicit AND finds nothing.

    "commercial pipeline" returns zero hits when the two words are almost
    never in a single short utterance, even when whole meetings are about
    exactly that.
    """
    database.upsert_meeting(row(recording_id=1, folder="f1", title="Pipeline review"))
    database.index_transcript(
        1,
        [
            {
                "speaker": {"display_name": "A"},
                "timestamp": "00:00:01",
                "text": "let us talk about the commercial side",
            },
            {
                "speaker": {"display_name": "B"},
                "timestamp": "00:00:09",
                "text": "the pipeline needs another look",
            },
        ],
    )
    # A meeting that has only one of the two words must not match.
    database.upsert_meeting(row(recording_id=2, folder="f2", title="Other"))
    database.index_transcript(
        2,
        [
            {
                "speaker": {"display_name": "C"},
                "timestamp": "00:00:02",
                "text": "the pipeline again, nothing commercial here",
            }
        ],
    )

    hits = database.search("commercial pipeline")
    assert hits, "multi-word search must find meetings containing all the words"
    assert {h["recording_id"] for h in hits} == {1, 2}


def test_a_word_only_one_meeting_has_narrows_the_result(database: Database) -> None:
    database.upsert_meeting(row(recording_id=1, folder="f1"))
    database.index_transcript(
        1, [{"speaker": {"display_name": "A"}, "timestamp": "00:00:01", "text": "alpha beta"}]
    )
    database.upsert_meeting(row(recording_id=2, folder="f2"))
    database.index_transcript(
        2, [{"speaker": {"display_name": "B"}, "timestamp": "00:00:01", "text": "alpha only"}]
    )
    hits = database.search("alpha beta")
    assert {h["recording_id"] for h in hits} == {1}


def test_a_single_word_still_uses_the_direct_match(database: Database) -> None:
    database.upsert_meeting(row(recording_id=1, folder="f1"))
    database.index_transcript(
        1, [{"speaker": {"display_name": "A"}, "timestamp": "00:00:01", "text": "unique term"}]
    )
    assert len(database.search("unique")) == 1
    assert database.search("nothinglikethis") == []
