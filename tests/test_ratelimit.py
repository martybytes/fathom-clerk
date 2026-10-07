"""The pacing maths, asserted directly against synthetic headers.

No HTTP and no sleeping: the numbers here are the observed header values
(10 requests per 60 seconds), and checking them through a mock server would mean
either sleeping out real windows or testing something other than those values.
"""

from __future__ import annotations

from fath.ratelimit import RateLimiter, RateSnapshot, header_int


def limiter(**kwargs: object) -> RateLimiter:
    defaults: dict[str, object] = {"min_interval": 0.0, "reserve": 2, "max_backoff": 120.0}
    defaults.update(kwargs)
    return RateLimiter(**defaults)  # type: ignore[arg-type]


def test_the_measured_live_case_paces_at_about_ten_seconds() -> None:
    """8 remaining of 10, 58s to reset, 2 held back -> ~9.7s per request.

    This is the observed header set. Spreading the six spendable requests
    across the rest of the window is what keeps a backfill from spending it
    in four seconds and stalling for the other fifty-six.
    """
    rl = limiter()
    rl.observe({"RateLimit-Limit": "10", "RateLimit-Remaining": "8", "RateLimit-Reset": "58"})
    assert 9.0 < rl._pace < 10.0
    assert rl.throttle_sleeps == 0


def test_the_pace_is_never_longer_than_the_window() -> None:
    """Waiting longer than the whole window to send one request is never right.

    max_backoff must not bound this: it caps waiting *after a failure*, and
    spacing requests is not a failure.
    """
    rl = limiter(max_backoff=1.0)
    rl.observe({"RateLimit-Limit": "10", "RateLimit-Remaining": "3", "RateLimit-Reset": "30"})
    assert rl._pace == 30.0


def test_the_pace_tightens_as_the_window_refills() -> None:
    rl = limiter()
    rl.observe({"RateLimit-Limit": "10", "RateLimit-Remaining": "3", "RateLimit-Reset": "50"})
    tight = rl._pace
    rl.observe({"RateLimit-Limit": "10", "RateLimit-Remaining": "9", "RateLimit-Reset": "50"})
    assert rl._pace < tight


def test_reaching_the_reserve_stalls_for_the_reset() -> None:
    rl = limiter(reserve=2)
    rl.observe({"RateLimit-Limit": "10", "RateLimit-Remaining": "2", "RateLimit-Reset": "1"})
    assert rl.throttle_sleeps == 1


def test_a_response_with_no_rate_headers_is_survivable() -> None:
    """Fathom documents these headers, but a proxy in between may strip them."""
    rl = limiter()
    rl.observe({})
    assert rl.snapshot.reason == "no rate headers"
    assert rl._pace == 0.0


def test_retry_after_wins_over_the_jittered_backoff() -> None:
    rl = limiter()
    assert rl.penalty(retry_after=7.0, attempt=5) == 7.0


def test_backoff_without_retry_after_is_jittered_and_capped() -> None:
    rl = limiter(max_backoff=10.0)
    waits = {rl.penalty(None, attempt=8) for _ in range(50)}
    assert all(0.0 <= w <= 10.0 for w in waits)
    # Full jitter, not a fixed exponential: identical values every time would
    # re-synchronise concurrent retries on every round.
    assert len(waits) > 1


def test_the_wait_callback_cannot_break_a_sync() -> None:
    """A reporting hook that raises must not take the whole run with it."""

    def explode(seconds: float, reason: str) -> None:
        raise RuntimeError("the UI went away")

    rl = limiter(on_wait=explode)
    rl._sleep(0.01, "pacing")  # must not raise


def test_header_int_tolerates_junk() -> None:
    assert header_int({"X": "12"}, "X") == 12
    assert header_int({"X": "12.7"}, "X") == 12
    assert header_int({"X": "not a number"}, "X") is None
    assert header_int({}, "X") is None
    assert header_int(None, "X") is None


def test_the_snapshot_serialises_for_the_ui() -> None:
    payload = RateSnapshot(limit=10, remaining=4, reset_sec=30, pace_sec=7.5).as_json()
    assert payload == {
        "limit": 10,
        "remaining": 4,
        "resetSec": 30,
        "paceSec": 7.5,
        "waitingSec": 0.0,
        "reason": "",
    }
