"""The sync engine: meetings in, folders and database rows out.

A generator that yields SyncEvent, so the CLI, the Textual UI, the web app and
the scheduled run all consume one stream rather than each re-deriving progress
from print statements. A check produces a result, not a printed line, and that
separation is what makes a second front end cheap.

Order of operations matters and is not arbitrary:

  1. re-import first, so folders already on disk are adopted for free,
  2. then fetch, writing and committing each meeting as it arrives.

Step 1 is what stops a reinstall, a moved output folder or a lost database from
costing a full re-download.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fath import config, db, keystore, logging_setup, naming, writers
from fath.api import ApiError, AuthError, FathomClient
from fath.naming import NamingRules
from fath.ratelimit import RateLimiter, RateSnapshot
from fath.writers import FileSelection

log = logging.getLogger(__name__)

STARTED = "started"
MEETING = "meeting"
SKIPPED = "skipped"
IMPORTED = "imported"
THROTTLED = "throttled"
ERROR = "error"
FINISHED = "finished"


@dataclass
class SyncCounts:
    seen: int = 0
    written: int = 0
    skipped: int = 0
    imported: int = 0
    errors: int = 0
    requests: int = 0

    def as_json(self) -> dict[str, int]:
        return {
            "seen": self.seen,
            "written": self.written,
            "skipped": self.skipped,
            "imported": self.imported,
            "errors": self.errors,
            "requests": self.requests,
        }


@dataclass(frozen=True)
class SyncEvent:
    kind: str
    message: str = ""
    index: int = 0
    # None until the last page: cursor pagination cannot count ahead, and
    # inventing a total would make the UI's progress bar lie.
    total: int | None = None
    folder: str = ""
    recording_id: int = 0
    counts: SyncCounts | None = None
    rate: RateSnapshot | None = None
    level: str = "info"

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "index": self.index,
            "total": self.total,
            "folder": self.folder,
            "recordingId": self.recording_id,
            "counts": self.counts.as_json() if self.counts else None,
            "rate": self.rate.as_json() if self.rate else None,
            "level": self.level,
        }


@dataclass
class SyncOptions:
    dry_run: bool = False
    overwrite: bool = False
    reimport: bool = True
    created_after: str = ""
    limit: int = 0
    selection: FileSelection = field(default_factory=FileSelection)

    @classmethod
    def from_config(cls, cfg: config.Config | None = None, **kwargs: Any) -> SyncOptions:
        conf = cfg or config.shared()
        base = cls(
            overwrite=conf.get_bool("sync.overwriteExisting"),
            reimport=conf.get_bool("sync.reimportBeforeSync", True),
            created_after=str(conf.get("sync.backfillSince", "") or ""),
            selection=FileSelection.from_config(conf),
        )
        for key, value in kwargs.items():
            setattr(base, key, value)
        return base

    def as_json(self) -> dict[str, Any]:
        return {
            "dryRun": self.dry_run,
            "overwrite": self.overwrite,
            "reimport": self.reimport,
            "createdAfter": self.created_after,
            "limit": self.limit,
            "files": {
                "transcript": self.selection.transcript,
                "summary": self.selection.summary,
                "actionItems": self.selection.action_items,
                "meetingJson": self.selection.meeting_json,
                "highlights": self.selection.highlights,
                "media": self.selection.media,
            },
        }


def _normalise_since(raw: str) -> str:
    """Accept a date or a timestamp; return something the API will take."""
    text = (raw or "").strip()
    if not text:
        return ""
    if len(text) == 10:  # YYYY-MM-DD
        return f"{text}T00:00:00Z"
    return text


def _meeting_row(meeting: dict[str, Any], folder_name: str, rules: NamingRules) -> db.MeetingRow:
    transcript = meeting.get("transcript") or []
    summary = meeting.get("default_summary") or {}
    return db.MeetingRow(
        recording_id=int(meeting.get("recording_id") or 0),
        title=meeting.get("title") or "",
        meeting_title=meeting.get("meeting_title") or "",
        started_at=naming.meeting_datetime(meeting, rules).isoformat(),
        created_at=str(meeting.get("created_at") or ""),
        folder=folder_name,
        attendees=writers.attendee_display_names(meeting),
        recorded_by=(meeting.get("recorded_by") or {}).get("name") or "",
        share_url=meeting.get("share_url") or "",
        url=meeting.get("url") or "",
        content_hash=writers.content_hash(meeting),
        fetched_at=db.utc_now(),
        has_transcript=bool(transcript),
        has_summary=bool(summary.get("markdown_formatted")),
        action_item_count=len(meeting.get("action_items") or []),
        transcript_chars=sum(len((i or {}).get("text") or "") for i in transcript),
    )


class SyncEngine:
    def __init__(
        self,
        cfg: config.Config | None = None,
        database: db.Database | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.cfg = cfg or config.shared()
        self.db = database or db.shared()
        self.cancelled = cancelled or (lambda: False)
        self.rules = NamingRules.from_config(self.cfg)
        self.counts = SyncCounts()
        self._pending_wait: tuple[float, str] | None = None

    # -- re-import ------------------------------------------------------- #

    def reimport(self, out_root: Path) -> Iterator[SyncEvent]:
        """Adopt folders already on disk. No API calls at all.

        Every row here is guarded against the folder name already belonging to
        a different recording. Without that guard this raised a bare
        sqlite3.IntegrityError naming neither meeting nor folder, and took the
        whole sync down before a single request was made -- which is how a
        stale row from an unrelated database blocked a batch import.
        """
        if not out_root.is_dir():
            return
        for folder in sorted(p for p in out_root.iterdir() if p.is_dir()):
            if self.cancelled():
                return
            meeting = self._read_folder(folder)
            if meeting is None:
                continue
            recording_id = int(meeting.get("recording_id") or 0)
            if not recording_id:
                continue
            if self.db.get_meeting(recording_id) is not None:
                continue

            owner = self.db.folder_owner(folder.name)
            if owner is not None and owner != recording_id:
                # The folder on disk is the truth; a database row claiming that
                # name for a different recording is stale. Clear the claim and
                # let the rightful owner take it, rather than refusing to
                # import or crashing.
                self.db.release_folder(owner)
                yield SyncEvent(
                    IMPORTED,
                    f"{folder.name} was recorded against meeting {owner}; "
                    f"reassigning it to {recording_id}",
                    folder=folder.name,
                    recording_id=recording_id,
                    counts=self.counts,
                    level="warning",
                )

            row = _meeting_row(meeting, folder.name, self.rules)
            # A folder restored from a stamp has no transcript to hash, so keep
            # whatever hash was recorded when it was written.
            if meeting.get("_stamp_hash"):
                row.content_hash = str(meeting["_stamp_hash"])
            try:
                self.db.upsert_meeting(row)
            except db.FolderTaken as exc:
                # Should be unreachable after the guard above, but a corrupt
                # database must degrade to skipping one folder rather than
                # aborting an import of several hundred.
                self.counts.errors += 1
                yield SyncEvent(
                    ERROR,
                    str(exc),
                    folder=folder.name,
                    recording_id=recording_id,
                    counts=self.counts,
                    level="error",
                )
                continue

            if meeting.get("transcript"):
                self.db.index_transcript(recording_id, meeting["transcript"])
            self.counts.imported += 1
            yield SyncEvent(
                IMPORTED,
                f"adopted {folder.name}",
                folder=folder.name,
                recording_id=recording_id,
                counts=self.counts,
            )

    def _read_folder(self, folder: Path) -> dict[str, Any] | None:
        """Reconstruct what we can from a folder, preferring the full payload."""
        payload = next(folder.glob("*meeting.json"), None)
        if payload is not None:
            try:
                data = json.loads(payload.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("recording_id"):
                    return data
            except (OSError, ValueError):
                pass

        stamp = folder / writers.STAMP_NAME
        if stamp.is_file():
            try:
                data = json.loads(stamp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            if isinstance(data, dict) and data.get("recording_id"):
                return {
                    "recording_id": data.get("recording_id"),
                    "title": data.get("title") or "",
                    "recording_start_time": data.get("started_at") or "",
                    "_stamp_hash": data.get("content_hash") or "",
                }
        return None

    # -- the run --------------------------------------------------------- #

    def run(self, options: SyncOptions | None = None) -> Iterator[SyncEvent]:
        opts = options or SyncOptions.from_config(self.cfg)
        out_root = self.cfg.output_root()

        api_key, key_source = keystore.resolve()
        if not api_key:
            yield SyncEvent(
                ERROR,
                "No Fathom API key. Set one with `fath key --set`, in the web app, "
                "or as FATHOM_PERSONAL_API_KEY in .env.",
                level="error",
            )
            return

        logging_setup.log_run_header(
            log,
            "sync" + (" (dry run)" if opts.dry_run else ""),
            output=out_root,
            key_source=key_source,
            since=opts.created_after or "(incremental)",
            files=", ".join(
                name
                for name, on in (
                    ("transcript", opts.selection.transcript),
                    ("summary", opts.selection.summary),
                    ("action-items", opts.selection.action_items),
                    ("meeting.json", opts.selection.meeting_json),
                    ("highlights", opts.selection.highlights),
                    ("media", opts.selection.media),
                )
                if on
            ),
        )
        yield SyncEvent(
            STARTED,
            f"output {out_root} | key from {key_source}"
            + (" | dry run, nothing will be written" if opts.dry_run else ""),
            counts=self.counts,
        )

        if not opts.dry_run:
            out_root.mkdir(parents=True, exist_ok=True)

        if opts.reimport:
            yield from self.reimport(out_root)
            if self.counts.imported:
                yield SyncEvent(
                    IMPORTED,
                    f"adopted {self.counts.imported} folder(s) already on disk, "
                    f"costing no API requests",
                    counts=self.counts,
                )

        limiter = RateLimiter.from_config(self.cfg, on_wait=self._note_wait)
        client = FathomClient.from_config(
            api_key, self.cfg, limiter=limiter, cancelled=self.cancelled
        )

        created_after = _normalise_since(opts.created_after or self._incremental_since())
        taken: dict[str, int] = {}

        try:
            for meeting in client.iter_meetings(
                created_after=created_after,
                include_transcript=True,
                include_summary=True,
                include_action_items=True,
                include_highlights=opts.selection.highlights,
                recorded_by=[str(x) for x in self.cfg.get_list("sync.recordedBy")] or None,
            ):
                if self.cancelled():
                    yield SyncEvent(FINISHED, "cancelled", counts=self.counts, level="warning")
                    return

                if self._pending_wait is not None:
                    seconds, reason = self._pending_wait
                    self._pending_wait = None
                    yield SyncEvent(
                        THROTTLED,
                        f"waiting {seconds:.0f}s: {reason}",
                        counts=self.counts,
                        rate=limiter.snapshot,
                        level="warning",
                    )

                self.counts.seen += 1
                self.counts.requests = limiter.requests_made
                yield from self._handle_meeting(meeting, opts, out_root, taken, client, limiter)

                if opts.limit and self.counts.seen >= opts.limit:
                    yield SyncEvent(FINISHED, f"stopped at limit {opts.limit}", counts=self.counts)
                    return

        except AuthError as exc:
            yield SyncEvent(ERROR, str(exc), counts=self.counts, level="error")
            return
        except ApiError as exc:
            if str(exc) == "cancelled":
                yield SyncEvent(FINISHED, "cancelled", counts=self.counts, level="warning")
                return
            self.counts.errors += 1
            yield SyncEvent(ERROR, str(exc), counts=self.counts, level="error")
            return

        self.counts.requests = limiter.requests_made
        log.info(
            "finished: %d written, %d unchanged, %d adopted, %d errors, %d requests",
            self.counts.written,
            self.counts.skipped,
            self.counts.imported,
            self.counts.errors,
            limiter.requests_made,
        )
        yield SyncEvent(
            FINISHED,
            f"{self.counts.written} written, {self.counts.skipped} unchanged, "
            f"{self.counts.imported} adopted, {limiter.requests_made} API requests",
            counts=self.counts,
            rate=limiter.snapshot,
        )

    def _handle_meeting(
        self,
        meeting: dict[str, Any],
        opts: SyncOptions,
        out_root: Path,
        taken: dict[str, int],
        client: FathomClient,
        limiter: RateLimiter,
    ) -> Iterator[SyncEvent]:
        recording_id = int(meeting.get("recording_id") or 0)
        digest = writers.content_hash(meeting)

        if not opts.overwrite and self.db.content_hash(recording_id) == digest:
            self.counts.skipped += 1
            existing = self.db.get_meeting(recording_id)
            yield SyncEvent(
                SKIPPED,
                f"unchanged: {existing.folder if existing else recording_id}",
                index=self.counts.seen,
                folder=existing.folder if existing else "",
                recording_id=recording_id,
                counts=self.counts,
            )
            return

        try:
            folder_name = naming.build_folder_name(meeting, self.rules, out_root)
        except ValueError as exc:
            self.counts.errors += 1
            yield SyncEvent(
                ERROR, str(exc), recording_id=recording_id, counts=self.counts, level="error"
            )
            return

        owner = taken.get(folder_name) or self.db.folder_owner(folder_name)
        if owner is not None and owner != recording_id:
            # Two identical recurring meetings on one day. Rebuild with the
            # suffix rather than appending it, so the length cap sees the final
            # name -- appending afterwards is what overflowed MAX_PATH before.
            folder_name = naming.build_folder_name(
                meeting,
                self.rules,
                out_root,
                naming.collision_suffix_for(self.rules, recording_id),
            )
        taken[folder_name] = recording_id

        # The include flags almost always deliver these inline; the fallback
        # sometimes returned empty, meaning those meetings genuinely
        # have none. Only pay for it when something is actually missing.
        if opts.selection.transcript and not meeting.get("transcript"):
            try:
                meeting["transcript"] = client.get_transcript(recording_id)
            except ApiError:
                meeting["transcript"] = []
        if opts.selection.summary and not meeting.get("default_summary"):
            try:
                meeting["default_summary"] = client.get_summary(recording_id) or {}
            except ApiError:
                meeting["default_summary"] = {}

        if opts.dry_run:
            self.counts.seen = self.counts.seen
            yield SyncEvent(
                MEETING,
                folder_name,
                index=self.counts.seen,
                folder=folder_name,
                recording_id=recording_id,
                counts=self.counts,
                rate=limiter.snapshot,
            )
            return

        try:
            writers.write_meeting(
                out_root / folder_name, meeting, folder_name, self.rules, opts.selection
            )
        except OSError as exc:
            self.counts.errors += 1
            yield SyncEvent(
                ERROR, str(exc), recording_id=recording_id, counts=self.counts, level="error"
            )
            return

        row = _meeting_row(meeting, folder_name, self.rules)
        # The hash of what the API *sent*, not of what we ended up with. The
        # fallback above mutates `meeting` by filling in a null transcript, so
        # re-hashing here would store a value the next run's raw payload can
        # never match -- and those meetings would be rewritten on every sync
        # forever, spending two extra requests each against a 10-per-minute
        # limit.
        row.content_hash = digest
        # Committed per meeting, not at the end: an interrupted backfill must
        # keep everything already written.
        try:
            self.db.upsert_meeting(row)
        except db.FolderTaken as exc:
            # The folder is written; only the bookkeeping failed. Report it and
            # keep going rather than losing the rest of the run.
            self.counts.errors += 1
            yield SyncEvent(
                ERROR,
                str(exc),
                folder=folder_name,
                recording_id=recording_id,
                counts=self.counts,
                level="error",
            )
            return
        if meeting.get("transcript"):
            self.db.index_transcript(recording_id, meeting["transcript"])

        self.counts.written += 1
        log.debug("wrote %s (recording %s)", folder_name, recording_id)
        yield SyncEvent(
            MEETING,
            folder_name,
            index=self.counts.seen,
            folder=folder_name,
            recording_id=recording_id,
            counts=self.counts,
            rate=limiter.snapshot,
        )

    def _incremental_since(self) -> str:
        """Where to resume from, with an overlap so nothing falls through.

        The overlap costs nothing: a re-fetched meeting whose content hash is
        unchanged is skipped without writing.
        """
        latest = self.db.latest_started_at()
        if not latest:
            return ""
        try:
            parsed = datetime.fromisoformat(latest.replace("Z", "+00:00"))
        except ValueError:
            return ""
        overlap = self.cfg.get_int("sync.overlapMinutes", 60)
        return (parsed.astimezone(timezone.utc) - timedelta(minutes=overlap)).isoformat()

    def _note_wait(self, seconds: float, reason: str) -> None:
        # The limiter sleeps on this thread, so the event cannot be yielded from
        # here. Stash it and emit at the next meeting boundary.
        if seconds >= 2.0:
            self._pending_wait = (seconds, reason)


def run_to_database(
    options: SyncOptions | None = None,
    cfg: config.Config | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Iterator[SyncEvent]:
    """Run a sync, recording it as a row in `runs` and its events in `run_events`.

    The web app streams from those tables rather than from this generator, so a
    browser tab closed mid-sync loses nothing and a reconnect replays.
    """
    conf = cfg or config.shared()
    database = db.shared()
    engine = SyncEngine(conf, database, cancelled)
    opts = options or SyncOptions.from_config(conf)
    run_id = database.start_run(opts.as_json())
    status = "done"

    try:
        for event in engine.run(opts):
            database.add_event(run_id, event.message, event.level, event.as_json())
            if event.kind == ERROR and event.level == "error":
                status = "failed"
            if event.kind == FINISHED and "cancelled" in event.message:
                status = "cancelled"
            yield event
    except Exception as exc:
        status = "failed"
        database.add_event(run_id, f"{type(exc).__name__}: {exc}", "error")
        log.exception("sync failed")
        yield SyncEvent(ERROR, f"{type(exc).__name__}: {exc}", level="error")
    finally:
        database.finish_run(
            run_id,
            status,
            meetings_seen=engine.counts.seen,
            written=engine.counts.written,
            skipped=engine.counts.skipped,
            errors=engine.counts.errors,
            requests_made=engine.counts.requests,
        )
