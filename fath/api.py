"""The Fathom API client.

Stdlib urllib.request, with an explicit config-driven timeout. Not the official
fathom-python SDK: it exposes only list_meetings, list_meeting_types, list_teams,
list_team_members, create_webhook and delete_webhook -- no transcript, no summary
and no download -- and its own docs say it is beta with breaking changes outside
a major version. Stdlib also keeps an unattended scheduled sync free of extra
dependencies.

The important shape: GET /meetings with the four include_* flags returns fully
populated meetings, so a full backfill costs one request per page of ten rather
than three requests per meeting. Per-recording endpoints provide a fallback;
an empty fallback response means the meeting genuinely has no transcript.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

from fath import config
from fath.ratelimit import RateLimiter, header_int

log = logging.getLogger(__name__)

USER_AGENT = "fathom-helper/0.1 (+https://github.com/martybytes/fathom-helper)"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # See docs/decisions.md: API credentials must never follow redirects.
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


class ApiError(RuntimeError):
    """A request that will not improve by being retried."""

    def __init__(self, message: str, status: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class AuthError(ApiError):
    """401/403 -- the key is missing, wrong, or lacks access."""


class FathomClient:
    def __init__(
        self,
        api_key: str,
        limiter: RateLimiter | None = None,
        base_url: str = "",
        timeout: float = 30.0,
        max_retries: int = 5,
        cancelled: Any = None,
    ) -> None:
        self.api_key = api_key
        self.limiter = limiter or RateLimiter()
        self.base_url = (base_url or "https://api.fathom.ai/external/v1").rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        # A callable returning True when the caller wants to stop. Checked between
        # attempts so a cancel during a 60-second rate-limit wait is honoured at
        # the next boundary rather than after the whole backfill.
        self.cancelled = cancelled or (lambda: False)

    @classmethod
    def from_config(
        cls, api_key: str, cfg: config.Config | None = None, **kwargs: Any
    ) -> FathomClient:
        conf = cfg or config.shared()
        return cls(
            api_key=api_key,
            base_url=str(conf.get("api.baseUrl")),
            timeout=conf.get_float("api.timeoutSec", 30.0),
            max_retries=conf.get_int("api.maxRetries", 5),
            **kwargs,
        )

    @property
    def requests_made(self) -> int:
        return self.limiter.requests_made

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = "?" + urllib.parse.urlencode(params, doseq=True) if params else ""
        url = f"{self.base_url}{path}{query}"

        for attempt in range(self.max_retries + 1):
            if self.cancelled():
                raise ApiError("cancelled")
            self.limiter.before()
            request = urllib.request.Request(
                url,
                headers={
                    "X-Api-Key": self.api_key,
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            try:
                opener = urllib.request.build_opener(_NoRedirect())
                with opener.open(request, timeout=self.timeout) as response:
                    self.limiter.observe(response.headers)
                    return json.loads(response.read().decode("utf-8"))

            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    self.limiter.note_rate_limited()
                    wait = self.limiter.penalty(header_int(exc.headers, "Retry-After"), attempt)
                    log.warning("429 rate limited; waiting %.1fs (attempt %d)", wait, attempt + 1)
                    self.limiter._sleep(wait, "rate limited (429)")
                    continue
                if 500 <= exc.code < 600:
                    if attempt >= self.max_retries:
                        raise ApiError(f"server error {exc.code}", exc.code) from exc
                    wait = self.limiter.penalty(None, attempt)
                    log.warning("HTTP %d; retrying in %.1fs", exc.code, wait)
                    self.limiter._sleep(wait, f"server error {exc.code}")
                    continue

                body = exc.read().decode("utf-8", "replace")[:400]
                if exc.code in (401, 403):
                    raise AuthError(
                        "Fathom rejected the API key"
                        + (" (no access to this recording)" if exc.code == 403 else ""),
                        exc.code,
                        body,
                    ) from exc
                raise ApiError(f"HTTP {exc.code} for {path}", exc.code, body) from exc

            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                if attempt >= self.max_retries:
                    raise ApiError(f"{type(exc).__name__}: {exc}") from exc
                wait = self.limiter.penalty(None, attempt)
                log.warning("%s; retrying in %.1fs", type(exc).__name__, wait)
                self.limiter._sleep(wait, type(exc).__name__)

        raise ApiError(f"exhausted {self.max_retries} retries for {path}")

    def iter_meetings(
        self,
        created_after: str = "",
        include_transcript: bool = True,
        include_summary: bool = True,
        include_action_items: bool = True,
        include_highlights: bool = False,
        recorded_by: list[str] | None = None,
        on_page: Any = None,
    ) -> Iterator[dict[str, Any]]:
        """Every meeting, fully populated, following next_cursor to exhaustion.

        A generator so the caller writes each meeting as it arrives: an
        interrupted backfill then keeps everything already on disk, and the UI
        gets progress rather than a ten-minute silence.
        """
        params: dict[str, Any] = {}
        if include_transcript:
            params["include_transcript"] = "true"
        if include_summary:
            params["include_summary"] = "true"
        if include_action_items:
            params["include_action_items"] = "true"
        if include_highlights:
            params["include_highlights"] = "true"
        if created_after:
            params["created_after"] = created_after
        if recorded_by:
            params["recorded_by[]"] = list(recorded_by)

        page = 0
        while True:
            page += 1
            payload = self._get("/meetings", params)
            items = payload.get("items") or []
            if on_page is not None:
                on_page(page, len(items))
            yield from items

            cursor = payload.get("next_cursor")
            if not cursor:
                return
            params["cursor"] = cursor

    def get_transcript(self, recording_id: int) -> list[dict[str, Any]]:
        payload = self._get(f"/recordings/{recording_id}/transcript")
        items = payload.get("transcript")
        return items if isinstance(items, list) else []

    def get_summary(self, recording_id: int) -> dict[str, Any] | None:
        payload = self._get(f"/recordings/{recording_id}/summary")
        summary = payload.get("summary")
        return summary if isinstance(summary, dict) else None

    def verify_key(self) -> dict[str, Any]:
        """One cheap request, so the UI can say the key works before a full sync.

        Returns the first page rather than a dedicated endpoint because Fathom
        has no /me: a successful list is the only proof the key is good.
        """
        payload = self._get("/meetings", {})
        items = payload.get("items") or []
        return {
            "ok": True,
            "visibleMeetings": len(items),
            "hasMore": bool(payload.get("next_cursor")),
        }
