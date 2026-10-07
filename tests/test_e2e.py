"""End to end: the CLI and the web server driving a real sync against a mock API.

These are the tests that would have caught the crash. Unit tests passed while
`fath sync` died, because nothing exercised the whole path -- config, reimport,
fetch, write, database, over folders that already existed with rows already in
place.

Marked `slow`; `scripts/check.ps1 -Quick` skips them, everything else runs them.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, request

import pytest

from fath import cli, config, db
from fath.commands import doctor, status
from fath.commands import sync as sync_cmd

pytestmark = pytest.mark.slow


def meeting_payload(recording_id: int, title: str, started: str, invitees: list[str]) -> dict:
    return {
        "recording_id": recording_id,
        "title": title,
        "meeting_title": title,
        "recording_start_time": started,
        "created_at": started,
        "url": f"https://fathom.video/calls/{recording_id}",
        "share_url": f"https://fathom.video/share/{recording_id}",
        "recorded_by": {"name": "Jules Example"},
        "calendar_invitees": [{"name": n} for n in invitees],
        "transcript": [
            {
                "speaker": {"display_name": invitees[0] if invitees else "Jules Example"},
                "timestamp": "00:00:11",
                "text": f"discussing {title} and the commercial pipeline",
            }
        ],
        "default_summary": {"template_name": "general", "markdown_formatted": f"# {title}"},
        "action_items": [],
    }


PAGE = {
    "limit": 3,
    "next_cursor": None,
    "items": [
        meeting_payload(
            101, "Weekly Demo Review", "2024-04-19T14:00:00Z", ["Blaire Example", "Jules Example"]
        ),
        meeting_payload(
            102, "Jules & Marley 1:1", "2024-04-21T15:00:00Z", ["Marley Example", "Jules Example"]
        ),
        meeting_payload(
            103,
            "AB | CD: Change Management / Risk",
            "2024-04-22T09:00:00Z",
            ["Sam Example", "Casey Example, Ph.D."],
        ),
    ],
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        body = json.dumps(PAGE if "/meetings" in self.path else {"error": "nope"}).encode()
        self.send_response(200 if "/meetings" in self.path else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("RateLimit-Limit", "10")
        self.send_header("RateLimit-Remaining", "8")
        self.send_header("RateLimit-Reset", "2")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def api() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def workspace(api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FATHOM_PERSONAL_API_KEY", "test-key")
    out = tmp_path / "Fathom_Meetings"
    config.shared().set_many(
        {"outputRoot": str(out), "api.baseUrl": api, "api.minIntervalSec": 0.0}
    )
    return out


# -- the full first run ------------------------------------------------------ #


def test_a_first_sync_writes_folders_and_is_reportable(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sync_cmd.main([]) == 0
    folders = sorted(p.name for p in workspace.iterdir() if p.is_dir())
    assert folders == [
        "2024-04-19_weekly-demo-review_blaire-jules",
        "2024-04-21_1-on-1_jules-marley",
        "2024-04-22_ab-cd-change-management-risk_casey-sam",
    ]
    capsys.readouterr()
    assert status.main([]) == 0
    assert "meetings      : 3" in capsys.readouterr().out


def test_a_second_sync_changes_nothing(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sync_cmd.main([])
    before = {p: p.stat().st_mtime_ns for p in workspace.rglob("*") if p.is_file()}
    capsys.readouterr()

    assert sync_cmd.main([]) == 0
    assert "0 written" in capsys.readouterr().out
    after = {p: p.stat().st_mtime_ns for p in workspace.rglob("*") if p.is_file()}
    assert after == before, "an unchanged meeting must not be rewritten"


# -- the crash, end to end --------------------------------------------------- #


def test_a_sync_over_folders_from_a_previous_install(workspace: Path) -> None:
    """Folders on disk, empty database. The common real case after a reinstall."""
    assert sync_cmd.main([]) == 0
    # Capture the path before resetting: db.shared() would re-open the file and
    # Windows will not let it be deleted while a handle is live.
    db_path = db.shared().path
    db.reset_shared()
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)

    assert sync_cmd.main([]) == 0
    assert db.shared().count_meetings() == 3


def test_a_stale_row_owning_a_real_folder_does_not_abort_the_run(workspace: Path) -> None:
    """The exact production crash.

    A leftover row -- a fake meeting left behind by a test writing outside its
    sandbox -- claimed the folder name that another meeting owns. reimport
    crashed with a bare IntegrityError and the whole sync died before making a
    single request.
    """
    assert sync_cmd.main([]) == 0
    database = db.shared()
    folder = "2024-04-19_weekly-demo-review_blaire-jules"

    with database.write() as conn:
        conn.execute("DELETE FROM meetings WHERE recording_id = 101")
    database.upsert_meeting(db.MeetingRow(recording_id=4242, folder=folder, title="fake"))
    assert database.folder_owner(folder) == 4242

    assert sync_cmd.main([]) == 0, "a stale row must not fail the run"
    assert database.folder_owner(folder) == 101, "the folder on disk wins"


def test_a_run_that_fails_still_closes_its_row(workspace: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A row left 'running' makes every later run refuse to start."""
    monkeypatch.setenv("FATHOM_PERSONAL_API_KEY", "")
    monkeypatch.setattr("fath.keystore.resolve", lambda *a, **k: ("", ""))
    sync_cmd.main([])
    assert db.shared().active_run() is None


# -- the dashboard, end to end ------------------------------------------------ #


def test_the_web_server_drives_a_whole_sync(workspace: Path) -> None:
    import socket

    from fath.server.app import TOKEN_HEADER, Server

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = Server(config.shared(), port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    for _ in range(100):
        try:
            request.urlopen(f"{base}/healthz", timeout=0.5).read()
            break
        except (error.URLError, OSError):
            time.sleep(0.05)

    try:
        started = request.Request(
            f"{base}/api/sync",
            data=b"{}",
            headers={"Content-Type": "application/json", TOKEN_HEADER: server.token},
            method="POST",
        )
        with request.urlopen(started, timeout=10) as response:
            run_id = json.loads(response.read())["runId"]

        for _ in range(200):
            run = db.shared().get_run(run_id)
            if run and run["status"] != "running":
                break
            time.sleep(0.1)

        run = db.shared().get_run(run_id)
        assert run is not None
        assert run["status"] == "done", f"sync ended {run['status']}"
        assert run["written"] == 3

        with request.urlopen(f"{base}/api/meetings", timeout=5) as response:
            assert json.loads(response.read())["total"] == 3

        with request.urlopen(f"{base}/api/search?q=commercial+pipeline", timeout=5) as response:
            assert json.loads(response.read())["hits"], "search must find synced content"
    finally:
        server.shutdown()


# -- the doctor tells the truth ---------------------------------------------- #


def test_doctor_is_clean_after_a_successful_sync(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sync_cmd.main([])
    capsys.readouterr()
    assert doctor.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    by_id = {c["check"]: c for c in payload["checks"]}
    assert by_id["db.open"]["status"] == "ok"
    assert by_id["output.exists"]["status"] == "ok"


def test_the_cli_returns_a_nonzero_code_when_a_sync_fails(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task Scheduler's last-result column is the only signal a scheduled run gives."""
    monkeypatch.setattr("fath.keystore.resolve", lambda *a, **k: ("", ""))
    assert sync_cmd.main([]) == 1


def test_search_from_the_cli_after_a_sync(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sync_cmd.main([])
    capsys.readouterr()
    from fath.commands import search

    assert search.main(["commercial", "pipeline"]) == 0
    assert "match" in capsys.readouterr().out


def test_the_global_home_flag_isolates_a_whole_run(
    api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two roots side by side must not see each other's meetings."""
    monkeypatch.setenv("FATHOM_PERSONAL_API_KEY", "test-key")
    other = tmp_path / "second-root"
    assert cli.main(["--home", str(other), "status"]) == 0
    assert (other / "state").is_dir()
