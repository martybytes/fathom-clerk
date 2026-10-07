"""The API client and rate limiter, against a local mock.

Offline and about two seconds. Proves the things a live run would otherwise only
prove by accident: pagination terminates, a 429 is retried using Retry-After
rather than the jittered backoff, the proactive throttle fires before the window
is spent, and an auth failure is distinguishable from a transport failure.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from fath.api import ApiError, AuthError, FathomClient
from fath.ratelimit import RateLimiter

PAGE_ONE = {
    "limit": 2,
    "next_cursor": "cursor-page-2",
    "items": [
        {"recording_id": 101, "title": "Weekly Demo Review"},
        {"recording_id": 102, "title": "ExampleCo"},
    ],
}
PAGE_TWO = {"limit": 1, "next_cursor": None, "items": [{"recording_id": 103, "title": "Parts"}]}


class MockState:
    def __init__(self) -> None:
        self.meetings_calls = 0
        self.redirect_to = ""
        self.redirect_code = 302
        self.throttled_once = False
        self.fail_next_with = 0
        self.seen_keys: list[str] = []
        self.seen_params: list[dict[str, list[str]]] = []


class Handler(BaseHTTPRequestHandler):
    state: MockState

    def log_message(self, *args: Any) -> None:
        pass  # the default logger writes to stderr and drowns pytest output

    def _send(
        self, code: int, payload: dict[str, Any], headers: dict[str, Any] | None = None
    ) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        state = self.server.state  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        state.seen_keys.append(self.headers.get("X-Api-Key", ""))
        if state.redirect_to:
            self._send(state.redirect_code, {}, {"Location": state.redirect_to})
            return

        # Small numbers on purpose: the pacing maths is asserted directly in
        # test_ratelimit.py, and real 58-second windows would make this suite
        # sleep for half a minute to prove nothing extra.
        healthy = {"RateLimit-Limit": 10, "RateLimit-Remaining": 8, "RateLimit-Reset": 2}

        if parsed.path.endswith("/transcript"):
            self._send(
                200,
                {
                    "transcript": [
                        {"speaker": {"display_name": "A"}, "timestamp": "00:00:01", "text": "hi"}
                    ]
                },
                healthy,
            )
            return
        if parsed.path.endswith("/summary"):
            self._send(
                200,
                {"summary": {"template_name": "general", "markdown_formatted": "notes"}},
                healthy,
            )
            return

        if parsed.path.endswith("/meetings"):
            if state.fail_next_with:
                code = state.fail_next_with
                state.fail_next_with = 0
                extra = {"Retry-After": 1} if code == 429 else {}
                self._send(code, {"error": "nope"}, extra)
                return
            if self.headers.get("X-Api-Key") != "test-key":
                self._send(401, {"error": "bad key"})
                return

            state.seen_params.append(query)
            state.meetings_calls += 1
            second_page = query.get("cursor") == ["cursor-page-2"]

            if second_page and not state.throttled_once:
                state.throttled_once = True
                # One request left: the proactive throttle must stall here rather
                # than spend it and risk a 429 on the next call.
                headers = {"RateLimit-Limit": 10, "RateLimit-Remaining": 1, "RateLimit-Reset": 1}
            else:
                headers = healthy
            self._send(200, PAGE_TWO if second_page else PAGE_ONE, headers)
            return

        self._send(404, {"error": "not found"})


@pytest.fixture
def server() -> Iterator[tuple[str, MockState]]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.state = MockState()  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", httpd.state  # type: ignore[attr-defined]
    finally:
        httpd.shutdown()
        httpd.server_close()


def make_client(base: str, **kwargs: Any) -> FathomClient:
    limiter = RateLimiter(min_interval=0.0, reserve=2, max_backoff=2.0)
    return FathomClient("test-key", limiter=limiter, base_url=base, timeout=5.0, **kwargs)


def test_pagination_follows_the_cursor_to_exhaustion(server: tuple[str, MockState]) -> None:
    base, state = server
    client = make_client(base)
    meetings = list(client.iter_meetings())
    assert [m["recording_id"] for m in meetings] == [101, 102, 103]
    assert state.meetings_calls == 2


def test_include_flags_are_sent(server: tuple[str, MockState]) -> None:
    base, state = server
    client = make_client(base)
    list(client.iter_meetings(include_highlights=True))
    first = state.seen_params[0]
    for flag in (
        "include_transcript",
        "include_summary",
        "include_action_items",
        "include_highlights",
    ):
        assert first.get(flag) == ["true"], flag


def test_highlights_are_omitted_when_not_wanted(server: tuple[str, MockState]) -> None:
    base, state = server
    list(make_client(base).iter_meetings(include_highlights=False))
    assert "include_highlights" not in state.seen_params[0]


def test_the_api_key_reaches_the_server(server: tuple[str, MockState]) -> None:
    base, state = server
    list(make_client(base).iter_meetings())
    assert state.seen_keys and all(k == "test-key" for k in state.seen_keys)


def test_a_429_is_retried_using_retry_after(server: tuple[str, MockState]) -> None:
    """Retry-After is honoured exactly, in preference to the jittered backoff."""
    base, state = server
    state.fail_next_with = 429
    client = make_client(base)
    meetings = list(client.iter_meetings())
    assert len(meetings) == 3
    assert client.limiter.rate_limit_hits == 1


def test_the_proactive_throttle_fires_before_the_window_is_spent(
    server: tuple[str, MockState],
) -> None:
    base, _ = server
    client = make_client(base)
    list(client.iter_meetings())
    assert client.limiter.throttle_sleeps == 1


def test_the_limiter_reads_the_headers_off_a_real_response(
    server: tuple[str, MockState],
) -> None:
    base, _ = server
    client = make_client(base)
    next(iter(client.iter_meetings()))
    assert client.limiter.snapshot.limit == 10
    assert client.limiter.snapshot.remaining == 8


def test_a_server_error_is_retried_then_raises(server: tuple[str, MockState]) -> None:
    base, state = server
    state.fail_next_with = 503
    client = make_client(base)
    assert len(list(client.iter_meetings())) == 3  # one retry was enough


def test_a_bad_key_raises_auth_error_not_a_generic_failure(
    server: tuple[str, MockState],
) -> None:
    """The UI has to tell 'your key is wrong' apart from 'the network blipped'."""
    base, _ = server
    limiter = RateLimiter(min_interval=0.0)
    client = FathomClient("wrong-key", limiter=limiter, base_url=base, timeout=5.0)
    with pytest.raises(AuthError):
        list(client.iter_meetings())


def test_a_404_is_not_retried(server: tuple[str, MockState]) -> None:
    base, _ = server
    client = make_client(base)
    with pytest.raises(ApiError):
        client._get("/nope")


def test_cancellation_stops_between_requests(server: tuple[str, MockState]) -> None:
    """Cancel must be honoured at the next boundary, not after the whole backfill."""
    base, _ = server
    stop = {"value": False}
    client = make_client(base, cancelled=lambda: stop["value"])
    iterator = client.iter_meetings()
    assert next(iterator)["recording_id"] == 101
    stop["value"] = True
    with pytest.raises(ApiError, match="cancelled"):
        list(iterator)


def test_the_per_recording_fallbacks_work(server: tuple[str, MockState]) -> None:
    base, _ = server
    client = make_client(base)
    assert len(client.get_transcript(101)) == 1
    assert (client.get_summary(101) or {})["template_name"] == "general"


def test_verify_key_reports_visibility(server: tuple[str, MockState]) -> None:
    base, _ = server
    result = make_client(base).verify_key()
    assert result["ok"] is True
    assert result["visibleMeetings"] == 2
    assert result["hasMore"] is True


def test_a_response_without_rate_headers_does_not_crash_the_limiter() -> None:
    limiter = RateLimiter(min_interval=0.0)
    limiter.observe({})
    assert limiter.snapshot.reason == "no rate headers"
    limiter.before()  # must not raise


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirect_never_forwards_api_key(server: tuple[str, MockState], code: int) -> None:
    """urllib followed redirects and forwarded X-Api-Key to another origin."""
    base, state = server
    destination = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    destination_state = MockState()
    destination.state = destination_state  # type: ignore[attr-defined]
    thread = threading.Thread(target=destination.serve_forever, daemon=True)
    thread.start()
    try:
        state.redirect_to = f"http://127.0.0.1:{destination.server_port}/meetings"
        state.redirect_code = code
        with pytest.raises(ApiError) as raised:
            make_client(base)._get("/meetings")
        assert raised.value.status == code
        assert destination_state.seen_keys == []
        assert state.seen_keys == ["test-key"]
    finally:
        destination.shutdown()
        destination.server_close()
