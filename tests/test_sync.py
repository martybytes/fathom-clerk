"""The sync engine end to end, against a mock API and a real temp folder."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from fath import config, db, sync, writers
from fath.sync import SyncEngine, SyncOptions

MEETINGS = [
    {
        "recording_id": 101,
        "title": "Weekly Demo Review",
        "meeting_title": "Weekly Demo Review",
        "recording_start_time": "2024-04-19T14:00:00Z",
        "created_at": "2024-04-19T14:00:00Z",
        "url": "https://fathom.video/calls/101",
        "share_url": "https://fathom.video/share/101",
        "recorded_by": {"name": "Jules Example"},
        "calendar_invitees": [{"name": "Blaire Example"}, {"name": "Jules Example"}],
        "transcript": [
            {
                "speaker": {"display_name": "Blaire Example"},
                "timestamp": "00:00:11",
                "text": "The commercial pipeline needs review.",
            }
        ],
        "default_summary": {"template_name": "general", "markdown_formatted": "notes"},
        "action_items": [],
    },
    {
        "recording_id": 102,
        "title": "Jules & Marley 1:1",
        "meeting_title": "Jules & Marley 1:1",
        "recording_start_time": "2024-04-21T15:00:00Z",
        "created_at": "2024-04-21T15:00:00Z",
        "url": "https://fathom.video/calls/102",
        "share_url": "https://fathom.video/share/102",
        "recorded_by": {"name": "Jules Example"},
        "calendar_invitees": [{"name": "Marley Example"}, {"name": "Jules Example"}],
        # Null inline: the per-recording fallback has to fill this in.
        "transcript": None,
        "default_summary": None,
        "action_items": [],
    },
]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("RateLimit-Limit", "10")
        self.send_header("RateLimit-Remaining", "8")
        self.send_header("RateLimit-Reset", "2")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        counter = self.server.calls  # type: ignore[attr-defined]
        if self.path.endswith("/transcript"):
            counter["transcript"] += 1
            self._send(
                200,
                {
                    "transcript": [
                        {
                            "speaker": {"display_name": "Marley Example"},
                            "timestamp": "00:00:03",
                            "text": "Fetched by the fallback.",
                        }
                    ]
                },
            )
            return
        if self.path.endswith("/summary"):
            counter["summary"] += 1
            self._send(200, {"summary": {"template_name": "general", "markdown_formatted": "s"}})
            return
        if "/meetings" in self.path:
            counter["meetings"] += 1
            self._send(200, {"limit": 2, "next_cursor": None, "items": MEETINGS})
            return
        self._send(404, {"error": "not found"})


@pytest.fixture
def api() -> Iterator[tuple[str, dict[str, int]]]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.calls = {"meetings": 0, "transcript": 0, "summary": 0}  # type: ignore[attr-defined]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", httpd.calls  # type: ignore[attr-defined]
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def configured(api: tuple[str, dict[str, int]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    base, calls = api
    monkeypatch.setenv("FATHOM_PERSONAL_API_KEY", "test-key")
    conf = config.shared()
    out = tmp_path / "meetings"
    conf.set_many(
        {
            "outputRoot": str(out),
            "api.baseUrl": base,
            "api.minIntervalSec": 0.0,
            "sync.reimportBeforeSync": False,
        }
    )
    return conf, out, calls


def _lose_the_database() -> None:
    """Delete the database file, not just the handle.

    reset_shared() only drops the cached connection; the file survives, so a
    test that only called it was proving nothing about rebuilding from disk.
    """
    from fath import paths

    db.reset_shared()
    for suffix in ("", "-wal", "-shm"):
        Path(str(paths.db_path()) + suffix).unlink(missing_ok=True)


def run(conf: config.Config, **kwargs: Any) -> list[sync.SyncEvent]:
    engine = SyncEngine(conf, db.shared(), kwargs.pop("cancelled", None))
    return list(engine.run(SyncOptions.from_config(conf, **kwargs)))


def test_a_sync_writes_one_folder_per_meeting(configured) -> None:  # type: ignore[no-untyped-def]
    conf, out, _ = configured
    events = run(conf)
    assert [e.kind for e in events][-1] == sync.FINISHED

    folders = sorted(p.name for p in out.iterdir())
    assert folders == [
        "2024-04-19_weekly-demo-review_blaire-jules",
        "2024-04-21_1-on-1_jules-marley",
    ]
    files = sorted(p.name for p in (out / folders[0]).iterdir())
    assert files == [
        ".fath.json",
        "2024-04-19_weekly-demo-review_blaire-jules_action-items.md",
        "2024-04-19_weekly-demo-review_blaire-jules_meeting.json",
        "2024-04-19_weekly-demo-review_blaire-jules_summary.md",
        "2024-04-19_weekly-demo-review_blaire-jules_transcript.md",
    ]


def test_one_request_covers_a_whole_page(configured) -> None:  # type: ignore[no-untyped-def]
    """The include flags are what keep a backfill inside 10 requests a minute."""
    conf, _, calls = configured
    run(conf)
    assert calls["meetings"] == 1


def test_the_fallback_only_fires_for_what_is_missing(configured) -> None:  # type: ignore[no-untyped-def]
    conf, out, calls = configured
    run(conf)
    # Meeting 101 arrived complete; only 102 was null.
    assert calls["transcript"] == 1
    assert calls["summary"] == 1
    text = (
        out / "2024-04-21_1-on-1_jules-marley" / "2024-04-21_1-on-1_jules-marley_transcript.md"
    ).read_text(encoding="utf-8")
    assert "Fetched by the fallback." in text


def test_an_unchanged_meeting_is_skipped_on_the_second_run(configured) -> None:  # type: ignore[no-untyped-def]
    conf, _, _ = configured
    run(conf)
    events = run(conf)
    kinds = [e.kind for e in events]
    assert kinds.count(sync.SKIPPED) == 2
    assert kinds.count(sync.MEETING) == 0


def test_overwrite_rewrites_even_when_unchanged(configured) -> None:  # type: ignore[no-untyped-def]
    conf, _, _ = configured
    run(conf)
    events = run(conf, overwrite=True)
    assert [e.kind for e in events].count(sync.MEETING) == 2


def test_a_dry_run_writes_nothing(configured) -> None:  # type: ignore[no-untyped-def]
    conf, out, _ = configured
    events = run(conf, dry_run=True)
    assert [e.kind for e in events].count(sync.MEETING) == 2
    assert not out.exists() or not list(out.iterdir())


def test_rows_are_committed_per_meeting_so_a_run_can_resume(configured) -> None:  # type: ignore[no-untyped-def]
    conf, _, _ = configured
    run(conf)
    database = db.shared()
    assert database.count_meetings() == 2
    stored = database.get_meeting(101)
    assert stored is not None
    assert stored.folder == "2024-04-19_weekly-demo-review_blaire-jules"
    assert stored.has_transcript and stored.transcript_chars > 0


def test_transcripts_are_indexed_for_search(configured) -> None:  # type: ignore[no-untyped-def]
    conf, _, _ = configured
    run(conf)
    hits = db.shared().search("pipeline")
    assert len(hits) == 1
    assert hits[0]["recording_id"] == 101


def test_cancelling_stops_the_run(configured) -> None:  # type: ignore[no-untyped-def]
    conf, _, _ = configured
    events = run(conf, cancelled=lambda: True)
    assert events[-1].kind == sync.FINISHED
    assert "cancelled" in events[-1].message


def test_a_missing_key_is_reported_not_raised(  # type: ignore[no-untyped-def]
    configured, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conf, _, _ = configured
    monkeypatch.delenv("FATHOM_PERSONAL_API_KEY", raising=False)
    monkeypatch.setattr(sync.keystore.paths, "env_file", lambda: tmp_path / "absent" / ".env")
    events = run(conf)
    assert events[0].kind == sync.ERROR
    assert "No Fathom API key" in events[0].message


def test_the_file_selection_is_honoured(configured) -> None:  # type: ignore[no-untyped-def]
    conf, out, _ = configured
    conf.set_many({"files.summary": False, "files.actionItems": False, "files.meetingJson": False})
    run(conf)
    folder = out / "2024-04-19_weekly-demo-review_blaire-jules"
    names = sorted(p.name for p in folder.iterdir())
    assert names == [".fath.json", "2024-04-19_weekly-demo-review_blaire-jules_transcript.md"]


def test_reimport_adopts_folders_without_any_api_calls(configured) -> None:  # type: ignore[no-untyped-def]
    """A lost database must be rebuildable from disk for free."""
    conf, out, calls = configured
    run(conf)
    before = calls["meetings"]

    _lose_the_database()
    (conf.output_root() / "not-a-meeting").mkdir(exist_ok=True)
    engine = SyncEngine(conf, db.shared())
    adopted = [e for e in engine.reimport(out)]

    assert len(adopted) == 2
    assert calls["meetings"] == before, "reimport must not touch the API"
    assert db.shared().count_meetings() == 2


def test_reimport_is_idempotent(configured) -> None:  # type: ignore[no-untyped-def]
    conf, out, _ = configured
    run(conf)
    _lose_the_database()
    engine = SyncEngine(conf, db.shared())
    assert len(list(engine.reimport(out))) == 2
    engine2 = SyncEngine(conf, db.shared())
    assert list(engine2.reimport(out)) == []


def test_reimport_works_from_the_stamp_alone(configured) -> None:  # type: ignore[no-untyped-def]
    """meeting.json can be turned off; the stamp is what keeps a rebuild free."""
    conf, out, _ = configured
    conf.set("files.meetingJson", False)
    run(conf)
    _lose_the_database()
    engine = SyncEngine(conf, db.shared())
    adopted = list(engine.reimport(out))
    assert len(adopted) == 2
    assert db.shared().get_meeting(101) is not None


def test_a_run_is_recorded_with_its_events(configured) -> None:  # type: ignore[no-untyped-def]
    conf, _, _ = configured
    list(sync.run_to_database(SyncOptions.from_config(conf), conf))
    database = db.shared()
    last = database.last_run()
    assert last is not None
    assert last["status"] == "done"
    assert last["written"] == 2
    assert database.active_run() is None
    assert len(database.events_since(last["id"])) > 2


def test_a_collision_gets_a_distinct_folder(configured) -> None:  # type: ignore[no-untyped-def]
    """Two identical recurring meetings on one day must not share a folder."""
    conf, out, _ = configured
    duplicate = json.loads(json.dumps(MEETINGS[0]))
    duplicate["recording_id"] = 999
    MEETINGS.append(duplicate)
    try:
        run(conf)
        folders = sorted(p.name for p in out.iterdir())
        assert len(folders) == 3
        assert any(f.endswith("-999") for f in folders)
    finally:
        MEETINGS.pop()


def test_the_content_hash_is_stable_across_runs(configured) -> None:  # type: ignore[no-untyped-def]
    conf, _, _ = configured
    run(conf)
    first = db.shared().content_hash(101)
    assert first == writers.content_hash(MEETINGS[0])


# -- the folder-collision crash --------------------------------------------- #


def test_reimport_survives_a_stale_row_holding_the_folder_name(configured) -> None:  # type: ignore[no-untyped-def]
    """The crash: `UNIQUE constraint failed: meetings.folder`.

    A leftover row from an unrelated database claimed the folder name that a
    meeting on disk owns. reimport had no guard, so a bare IntegrityError
    naming neither meeting nor folder took down the whole import before a single
    API request was made.
    """
    conf, out, _ = configured
    run(conf)  # writes two folders and their rows

    folder = "2024-04-19_weekly-demo-review_blaire-jules"
    database = db.shared()
    real = database.folder_owner(folder)
    assert real == 101

    # Simulate the stale claim: a different recording owning that exact name.
    database.upsert_meeting(db.MeetingRow(recording_id=1, folder=""))
    with database.write() as conn:
        conn.execute("DELETE FROM meetings WHERE recording_id = 101")
        conn.execute("UPDATE meetings SET folder = ? WHERE recording_id = 1", (folder,))
    assert database.folder_owner(folder) == 1

    engine = SyncEngine(conf, database)
    events = list(engine.reimport(out))

    assert any("reassigning" in e.message for e in events), "must explain what it did"
    assert database.folder_owner(folder) == 101, "the folder on disk is the truth"
    assert database.get_meeting(101) is not None


def test_a_folder_collision_names_the_meetings_involved() -> None:
    """The bare sqlite error named neither, which is why it needed a db dump."""
    from fath.db import FolderTaken

    exc = FolderTaken(recording_id=123456789, folder="2024-04-19_weekly", owner=4242)
    text = str(exc)
    assert "123456789" in text and "4242" in text and "2024-04-19_weekly" in text


def test_upsert_raises_the_helpful_error_not_the_bare_one(tmp_path: Path) -> None:
    from fath.db import Database, FolderTaken, MeetingRow

    database = Database(tmp_path / "x.db")
    database.upsert_meeting(MeetingRow(recording_id=1, folder="shared"))
    with pytest.raises(FolderTaken) as caught:
        database.upsert_meeting(MeetingRow(recording_id=2, folder="shared"))
    assert caught.value.owner == 1
    assert caught.value.recording_id == 2


def test_releasing_a_folder_frees_the_name(tmp_path: Path) -> None:
    from fath.db import Database, MeetingRow

    database = Database(tmp_path / "x.db")
    database.upsert_meeting(MeetingRow(recording_id=1, folder="shared"))
    database.release_folder(1)
    assert database.folder_owner("shared") is None
    database.upsert_meeting(MeetingRow(recording_id=2, folder="shared"))
    assert database.folder_owner("shared") == 2
    assert database.get_meeting(1) is not None, "the row survives, only the claim goes"
