"""The web server: its security posture first, then its routes.

The security tests come first because this is an HTTP server that holds an API
key and can write files, listening on a port any page in the browser can reach.
Three things keep that acceptable, and each is asserted rather than assumed:
loopback-only binding, a Host allowlist, and a token on every mutating route.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib import error, request

import pytest

from fath import config, db, keystore, paths
from fath.server.app import TOKEN_HEADER, Server


@pytest.fixture
def server(isolated_home: Path) -> Iterator[Server]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    instance = Server(config.shared(), port)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()

    for _ in range(100):
        try:
            request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.5).read()
            break
        except (error.URLError, OSError):
            time.sleep(0.05)
    try:
        yield instance
    finally:
        instance.shutdown()


def url(server: Server, path: str) -> str:
    return f"http://127.0.0.1:{server.port}{path}"


def get(server: Server, path: str, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    req = request.Request(url(server, path), headers=headers or {})
    try:
        with request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read() or b"{}")
    except error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body or b"{}")
        except ValueError:
            return exc.code, {"raw": body.decode("utf-8", "replace")}


def post(
    server: Server,
    path: str,
    payload: Any = None,
    token: str | None = "auto",
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    sent = {"Content-Type": "application/json", **(headers or {})}
    if token == "auto":
        sent[TOKEN_HEADER] = server.token
    elif token:
        sent[TOKEN_HEADER] = token
    body = json.dumps(payload or {}).encode()
    req = request.Request(url(server, path), data=body, headers=sent, method="POST")
    try:
        with request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"{}")
        except ValueError:
            return exc.code, {"raw": raw.decode("utf-8", "replace")}


# -- security ---------------------------------------------------------------- #


def test_it_binds_loopback_only(server: Server) -> None:
    """Never 0.0.0.0: nothing on the network may reach this."""
    assert server.httpd is not None
    assert server.httpd.server_address[0] == "127.0.0.1"


@pytest.mark.parametrize("route", ["/", "/api/status"])
def test_dashboard_cannot_be_framed_or_cached(server: Server, route: str) -> None:
    """A hostile page could frame the dashboard and trick users into local writes."""
    with request.urlopen(url(server, route), timeout=5) as response:
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.parametrize("route", ["/api/key/verify", "/api/naming/preview", "/api/future"])
def test_all_posts_require_token_by_default(server: Server, route: str) -> None:
    """The write-route allowlist left key verification and future routes unprotected."""
    status, _ = post(server, route, token=None)
    assert status == 401


def test_a_mutating_route_without_a_token_is_refused(server: Server) -> None:
    """Otherwise any page you visit could start a sync or rewrite your key."""
    status, payload = post(server, "/api/sync", {"dryRun": True}, token=None)
    assert status == 401
    assert TOKEN_HEADER in payload["error"]


def test_a_mutating_route_with_the_wrong_token_is_refused(server: Server) -> None:
    status, _ = post(server, "/api/sync", {"dryRun": True}, token="0" * 64)
    assert status == 401


def test_every_write_route_is_covered_by_the_token_check(server: Server) -> None:
    """A new write route added without a token check is the failure this catches."""
    for route in ("/api/settings", "/api/key", "/api/sync", "/api/sync/cancel", "/api/import"):
        status, _ = post(server, route, {}, token=None)
        assert status == 401, f"{route} accepted a request with no token"


def test_a_forged_host_header_is_refused(server: Server) -> None:
    """DNS rebinding: an attacker's domain can resolve to 127.0.0.1.

    The Host header is what gives that away, and it is the only signal available
    -- the request otherwise looks entirely local.
    """
    status, payload = get(server, "/api/status", {"Host": "evil.example.com"})
    assert status == 403
    assert "Host" in payload["error"]


def test_localhost_and_the_bound_address_are_accepted(server: Server) -> None:
    for host in (f"127.0.0.1:{server.port}", f"localhost:{server.port}"):
        status, _ = get(server, "/api/status", {"Host": host})
        assert status == 200, host


def test_no_cors_headers_are_ever_sent(server: Server) -> None:
    """Their absence is what stops another origin reading the token."""
    req = request.Request(url(server, "/api/status"))
    with request.urlopen(req, timeout=5) as response:
        headers = {k.lower() for k in response.headers}
    assert not any(h.startswith("access-control-") for h in headers)


def test_the_key_is_never_returned_to_the_page(
    server: Server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = tmp_path / ".env"
    monkeypatch.setattr(keystore.paths, "env_file", lambda: env)
    secret = "supersecretvalue98765"
    status, payload = post(server, "/api/key", {"key": secret})
    assert status == 200
    assert secret not in json.dumps(payload)
    assert payload["key"]["tail"] == "8765"


def test_a_traversal_outside_the_web_root_is_refused(server: Server) -> None:
    status, _ = get(server, "/../../../fath/keystore.py")
    assert status in (403, 404), "must not serve files outside web/dist"


def test_an_oversized_body_is_rejected_rather_than_buffered(server: Server) -> None:
    huge = {"key": "x" * (600 * 1024)}
    status, _ = post(server, "/api/key", huge)
    assert status == 400


def test_malformed_json_is_a_400_not_a_500(server: Server) -> None:
    req = request.Request(
        url(server, "/api/settings"),
        data=b"{not json",
        headers={"Content-Type": "application/json", TOKEN_HEADER: server.token},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=5) as response:
            status = response.status
    except error.HTTPError as exc:
        status = exc.code
    assert status == 400


# -- reads ------------------------------------------------------------------- #


def test_healthz_is_unauthenticated(server: Server) -> None:
    status, payload = get(server, "/healthz")
    assert status == 200 and payload["ok"] is True


def test_status_reports_what_the_dashboard_needs(server: Server) -> None:
    status, payload = get(server, "/api/status")
    assert status == 200
    for field in ("settingsRoot", "outputRoot", "key", "meetings", "rateLimit"):
        assert field in payload
    assert payload["rateLimit"]["requestsPerWindow"] == 10
    assert payload["rateLimit"]["windowSeconds"] == 60


def test_meetings_paginate_and_filter(server: Server) -> None:
    database = db.shared()
    for i in range(1, 6):
        database.upsert_meeting(
            db.MeetingRow(
                recording_id=i,
                title="ExampleCo" if i % 2 else "Other",
                folder=f"2024-04-0{i}_f{i}",
                started_at=f"2024-04-0{i}T10:00:00Z",
            )
        )
    status, payload = get(server, "/api/meetings?limit=2")
    assert status == 200
    assert payload["total"] == 5 and len(payload["items"]) == 2

    _, filtered = get(server, "/api/meetings?q=exampleco")
    assert filtered["total"] == 3


def test_a_meeting_detail_includes_the_rendered_markdown(
    server: Server, isolated_home: Path
) -> None:
    out = isolated_home / "meetings"
    folder = out / "2024-04-19_weekly_blaire"
    folder.mkdir(parents=True)
    (folder / "2024-04-19_weekly_blaire_transcript.md").write_text(
        "---\ntitle: x\n---\n\n**[00:00:01] Blaire:** hello", encoding="utf-8"
    )
    config.shared().set("outputRoot", str(out))
    db.shared().upsert_meeting(db.MeetingRow(recording_id=7, title="Weekly", folder=folder.name))

    status, payload = get(server, "/api/meetings/7")
    assert status == 200
    assert "Blaire" in payload["transcript"]
    assert payload["files"]


def test_an_unknown_meeting_is_a_404(server: Server) -> None:
    status, _ = get(server, "/api/meetings/999999")
    assert status == 404


def test_settings_are_served_with_their_winning_layer(server: Server) -> None:
    status, payload = get(server, "/api/settings")
    assert status == 200
    by_key = {s["key"]: s for s in payload["settings"]}
    assert by_key["naming.maxAttendees"]["source"] == "default"
    assert "Naming" in payload["groups"]


def test_search_returns_hits(server: Server) -> None:
    database = db.shared()
    database.upsert_meeting(db.MeetingRow(recording_id=1, title="Weekly", folder="f"))
    database.index_transcript(
        1, [{"speaker": {"display_name": "A"}, "timestamp": "00:00:01", "text": "the pipeline"}]
    )
    status, payload = get(server, "/api/search?q=pipeline")
    assert status == 200 and len(payload["hits"]) == 1


# -- writes ------------------------------------------------------------------ #


def test_settings_are_saved_through_the_one_writer(server: Server) -> None:
    status, payload = post(server, "/api/settings", {"naming.maxAttendees": 3})
    assert status == 200
    assert config.shared().get_int("naming.maxAttendees") == 3
    assert "naming.maxAttendees" in payload["saved"]


def test_an_invalid_setting_is_refused_with_every_reason(server: Server) -> None:
    status, payload = post(
        server, "/api/settings", {"naming.maxAttendees": 99, "naming.dateSource": "nope"}
    )
    assert status == 422
    assert "naming.maxAttendees" in payload["error"]
    assert "naming.dateSource" in payload["error"]
    assert config.shared().get_int("naming.maxAttendees") == 5


def test_an_unknown_setting_cannot_be_written(server: Server) -> None:
    """The settings endpoint must not be a way to write arbitrary config."""
    status, _ = post(server, "/api/settings", {"nosuch.key": 1})
    assert status == 422


def test_the_naming_preview_does_not_save(server: Server) -> None:
    db.shared().upsert_meeting(
        db.MeetingRow(
            recording_id=1,
            title="Weekly Demo Review",
            folder="2024-04-19_weekly-demo-review_blaire",
            started_at="2024-04-19T14:00:00Z",
            attendees=["Blaire Example", "Jules Example", "Marley Example"],
        )
    )
    status, payload = post(server, "/api/naming/preview", {"settings": {"naming.maxAttendees": 1}})
    assert status == 200
    assert payload["items"]
    assert "-and-" in payload["items"][0]["proposed"]
    assert config.shared().get_int("naming.maxAttendees") == 5, "a preview must not save"


def test_the_preview_reports_the_derived_name_limit(server: Server) -> None:
    """The cap depends on the output root, and the UI has to be able to say so."""
    _, payload = post(server, "/api/naming/preview", {})
    assert isinstance(payload["maxNameLength"], int)
    assert payload["maxNameLength"] > 0


def test_a_sync_starts_and_reports_a_run_id(
    server: Server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("FATHOM_PERSONAL_API_KEY", raising=False)
    monkeypatch.setattr(keystore.paths, "env_file", lambda: tmp_path / "absent" / ".env")
    status, payload = post(server, "/api/sync", {"dryRun": True})
    assert status == 202
    assert isinstance(payload["runId"], int)


def test_two_syncs_at_once_are_refused(server: Server) -> None:
    """A backfill is minutes long; stacking them would double every request."""
    import threading

    release = threading.Event()
    busy = threading.Thread(target=release.wait, daemon=True)
    busy.start()
    server.runner.thread = busy
    try:
        status, payload = post(server, "/api/sync", {"dryRun": True})
        assert status == 409
        assert "already running" in payload["error"]
    finally:
        release.set()
        busy.join(timeout=5)
        server.runner.thread = None


def test_import_adopts_folders_with_no_api_calls(server: Server, tmp_path: Path) -> None:
    folder = tmp_path / "existing" / "2024-04-19_weekly_blaire"
    folder.mkdir(parents=True)
    (folder / "meeting.json").write_text(
        json.dumps(
            {
                "recording_id": 5150,
                "title": "Weekly",
                "recording_start_time": "2024-04-19T14:00:00Z",
                "recorded_by": {"name": "Jules"},
                "calendar_invitees": [{"name": "Blaire Example"}],
            }
        ),
        encoding="utf-8",
    )
    status, payload = post(server, "/api/import", {"path": str(tmp_path / "existing")})
    assert status == 200 and payload["adopted"] == 1
    assert db.shared().get_meeting(5150) is not None


def test_importing_a_missing_folder_says_which(server: Server, tmp_path: Path) -> None:
    status, payload = post(server, "/api/import", {"path": str(tmp_path / "nope")})
    assert status == 400
    assert "nope" in payload["error"]


def test_an_unknown_route_is_a_404_shaped_like_every_other_error(server: Server) -> None:
    status, payload = post(server, "/api/nonsense", {})
    assert status == 404
    assert "error" in payload


# -- streaming ---------------------------------------------------------------- #


def test_the_event_stream_replays_from_the_database(server: Server) -> None:
    """A tab closed mid-sync must lose nothing when it comes back."""
    database = db.shared()
    run_id = database.start_run({})
    database.add_event(run_id, "started")
    database.add_event(run_id, "wrote a-folder")
    database.finish_run(run_id, "done", written=1)

    req = request.Request(url(server, f"/api/sync/stream?run_id={run_id}"))
    with request.urlopen(req, timeout=10) as response:
        text = ""
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            chunk = response.readline().decode("utf-8", "replace")
            text += chunk
            if "event: done" in text:
                break
    assert "wrote a-folder" in text
    assert "event: done" in text


# -- static ------------------------------------------------------------------- #


def test_a_missing_bundle_explains_how_to_build_it(server: Server) -> None:
    """A blank page served by a healthy server is the worst failure mode."""
    from fath.server import app as server_app

    if server_app.WEB_DIST.is_dir():
        pytest.skip("the bundle is built, so the guidance page cannot show")
    status, payload = get(server, "/")
    assert status == 503
    assert "npm run build" in payload["raw"]


def test_the_token_is_written_to_the_settings_root(isolated_home: Path) -> None:
    from fath.server.app import load_or_create_token

    first = load_or_create_token()
    assert len(first) == 64
    assert paths.token_path().read_text(encoding="utf-8").strip() == first
    assert load_or_create_token() == first, "the token must be stable across restarts"


def test_the_doctor_endpoint_mirrors_the_cli(server: Server) -> None:
    """The dashboard showed a clean overview for days while the CLI was failing.

    Serving the same checks is what closes that gap, so the endpoint has to stay
    wired to doctor.run_checks() rather than growing its own opinion.
    """
    status, payload = get(server, "/api/doctor")
    assert status == 200
    assert isinstance(payload["ok"], bool)
    by_id = {c["check"]: c for c in payload["checks"]}
    assert "key.present" in by_id and "db.open" in by_id
    for check in payload["checks"]:
        assert {"check", "status", "message", "hint"} <= set(check)


def test_the_doctor_endpoint_reports_a_missing_key_as_not_ok(
    server: Server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("FATHOM_PERSONAL_API_KEY", raising=False)
    monkeypatch.setattr(keystore.paths, "env_file", lambda: tmp_path / "absent" / ".env")
    _, payload = get(server, "/api/doctor")
    assert payload["ok"] is False
    by_id = {c["check"]: c for c in payload["checks"]}
    assert by_id["key.present"]["status"] == "fail"
    assert by_id["key.present"]["hint"], "a failure must carry its remedy"
