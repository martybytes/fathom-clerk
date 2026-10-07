"""Staying inside Fathom's rate limit, which Fathom does not publish.

The docs describe RateLimit-Limit / -Remaining / -Reset on every response and
Retry-After on a 429, but state no number. Observed headers report **10 requests
per 60 seconds**. That is tight enough to change the design, so nothing here
assumes it: the numbers are read from each response and the pace follows them.

Two rules:

  * Spread the remaining requests across the rest of the window, rather than
    spending them as fast as possible. A fixed 0.4s floor burns the whole window
    in four seconds and then stalls for fifty-six. The throughput is identical,
    but every window ends pressed against the limit with no room for a retry.
  * Stop and wait while `reserve` requests are still unspent, so a 429 is the
    exception rather than the mechanism.

A time-boxed cache is right for an optional status read where stale beats
nothing. It is wrong here: a bulk sync has no cache to fall back on, and a
dropped page means missing meetings. The limiter therefore retries, with full
jitter and a cap.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any

from fath import config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateSnapshot:
    """What the last response said, for the UI's rate meter."""

    limit: int | None = None
    remaining: int | None = None
    reset_sec: int | None = None
    pace_sec: float = 0.0
    waiting_sec: float = 0.0
    reason: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "remaining": self.remaining,
            "resetSec": self.reset_sec,
            "paceSec": round(self.pace_sec, 2),
            "waitingSec": round(self.waiting_sec, 2),
            "reason": self.reason,
        }


def header_int(headers: Any, name: str) -> int | None:
    try:
        raw = headers.get(name)
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


class RateLimiter:
    """Header-driven throttle. Serial by design; a single-user sync needs no speed."""

    def __init__(
        self,
        min_interval: float = 1.0,
        reserve: int = 2,
        max_backoff: float = 120.0,
        on_wait: Any = None,
    ) -> None:
        self.min_interval = max(0.0, min_interval)
        self.reserve = max(0, reserve)
        self.max_backoff = max(1.0, max_backoff)
        self.on_wait = on_wait  # called as (seconds, reason) so a UI can show it
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._pace = 0.0
        self.snapshot = RateSnapshot()
        self.throttle_sleeps = 0
        self.rate_limit_hits = 0
        self.requests_made = 0

    @classmethod
    def from_config(cls, cfg: config.Config | None = None, on_wait: Any = None) -> RateLimiter:
        conf = cfg or config.shared()
        return cls(
            min_interval=conf.get_float("api.minIntervalSec", 1.0),
            reserve=conf.get_int("api.reserveRequests", 2),
            max_backoff=conf.get_float("api.maxBackoffSec", 120.0),
            on_wait=on_wait,
        )

    def _sleep(self, seconds: float, reason: str) -> None:
        if seconds <= 0:
            return
        self.snapshot = RateSnapshot(
            limit=self.snapshot.limit,
            remaining=self.snapshot.remaining,
            reset_sec=self.snapshot.reset_sec,
            pace_sec=self._pace,
            waiting_sec=seconds,
            reason=reason,
        )
        if self.on_wait is not None:
            try:
                self.on_wait(seconds, reason)
            except Exception:
                log.debug("rate-limit callback failed", exc_info=True)
        time.sleep(seconds)

    def before(self) -> None:
        """Hold the pace before issuing a request."""
        with self._lock:
            wait = max(self.min_interval, self._pace)
            gap = time.monotonic() - self._last_call
            remaining = wait - gap if self._last_call else 0.0
        if remaining > 0:
            self._sleep(remaining, "pacing")
        with self._lock:
            self._last_call = time.monotonic()

    def observe(self, headers: Any) -> None:
        """Learn from a response, and stall if the window is nearly spent."""
        self.requests_made += 1
        limit = header_int(headers, "RateLimit-Limit")
        remaining = header_int(headers, "RateLimit-Remaining")
        reset = header_int(headers, "RateLimit-Reset")

        if remaining is None:
            self.snapshot = RateSnapshot(pace_sec=self._pace, reason="no rate headers")
            return

        if reset and remaining > self.reserve:
            # Bounded by the reset window, not by max_backoff: those are different
            # ideas. max_backoff caps how long to wait *after a failure*; spacing
            # requests is not a failure, and its natural ceiling is the window --
            # waiting longer than the whole window to send one request is never
            # right.
            self._pace = min(float(reset), reset / max(1, remaining - self.reserve))

        self.snapshot = RateSnapshot(
            limit=limit, remaining=remaining, reset_sec=reset, pace_sec=self._pace
        )
        log.debug(
            "rate limit %s/%s remaining, resets in %ss, pacing %.1fs",
            remaining,
            limit,
            reset,
            self._pace,
        )

        if remaining <= self.reserve and reset:
            self.throttle_sleeps += 1
            # +1 so the window has definitely rolled; landing exactly on the
            # boundary just earns a 429 and the backoff path instead.
            self._sleep(reset + 1, f"window nearly spent ({remaining} left)")

    def penalty(self, retry_after: float | None, attempt: int) -> float:
        """How long to wait after a failure."""
        if retry_after is not None:
            return max(0.0, retry_after)
        # Full jitter. Without it, concurrent retries re-synchronise every round.
        return random.uniform(0, min(self.max_backoff, 2.0**attempt))

    def note_rate_limited(self) -> None:
        self.rate_limit_hits += 1
