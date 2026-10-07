"""SQLite: what has been fetched, what each run did, and the search index.

Replaces the JSON state file the first draft called for. The reason is the web
UI: sorting, filtering and paging many meetings out of a JSON document means
loading and re-parsing all of it on every keystroke, and full-text search across
transcripts has no honest JSON answer at all.

Three things the schema exists to support:

  * resume -- a row is committed per meeting, so an interrupted backfill keeps
    everything already written and the next run skips it,
  * skip-unchanged -- content_hash means a re-fetch of an unmodified meeting
    rewrites nothing,
  * replay -- run_events is both the live log the UI streams and the transcript
    of a run that finished while the tab was closed.

WAL mode because the web server reads on handler threads while a sync writes on
its own; the default rollback journal makes those block each other.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fath import paths

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# FTS5 ships with the CPython amalgamation on Windows and macOS, but a
# distro-built python3 can omit it. Search degrades to LIKE rather than the
# import failing, because losing search is a nuisance and failing to open the
# database at all is not.
_fts_available: bool | None = None


@dataclass
class MeetingRow:
    recording_id: int
    title: str = ""
    meeting_title: str = ""
    started_at: str = ""
    created_at: str = ""
    folder: str = ""
    attendees: list[str] = field(default_factory=list)
    recorded_by: str = ""
    share_url: str = ""
    url: str = ""
    content_hash: str = ""
    fetched_at: str = ""
    has_transcript: bool = False
    has_summary: bool = False
    action_item_count: int = 0
    transcript_chars: int = 0
    media_state: str = "none"

    def as_json(self) -> dict[str, Any]:
        return {
            "recording_id": self.recording_id,
            "title": self.title,
            "meeting_title": self.meeting_title,
            "started_at": self.started_at,
            "created_at": self.created_at,
            "folder": self.folder,
            "attendees": self.attendees,
            "recorded_by": self.recorded_by,
            "share_url": self.share_url,
            "url": self.url,
            "fetched_at": self.fetched_at,
            "has_transcript": self.has_transcript,
            "has_summary": self.has_summary,
            "action_item_count": self.action_item_count,
            "transcript_chars": self.transcript_chars,
            "media_state": self.media_state,
        }


def _row_to_meeting(row: sqlite3.Row) -> MeetingRow:
    try:
        attendees = json.loads(row["attendees_json"] or "[]")
    except ValueError:
        attendees = []
    return MeetingRow(
        recording_id=int(row["recording_id"]),
        title=row["title"] or "",
        meeting_title=row["meeting_title"] or "",
        started_at=row["started_at"] or "",
        created_at=row["created_at"] or "",
        folder=row["folder"] or "",
        attendees=attendees if isinstance(attendees, list) else [],
        recorded_by=row["recorded_by"] or "",
        share_url=row["share_url"] or "",
        url=row["url"] or "",
        content_hash=row["content_hash"] or "",
        fetched_at=row["fetched_at"] or "",
        has_transcript=bool(row["has_transcript"]),
        has_summary=bool(row["has_summary"]),
        action_item_count=int(row["action_item_count"] or 0),
        transcript_chars=int(row["transcript_chars"] or 0),
        media_state=row["media_state"] or "none",
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FolderTaken(Exception):
    """Another recording already owns this folder name.

    Raised instead of letting sqlite3.IntegrityError escape. The bare error says
    only "UNIQUE constraint failed: meetings.folder" -- no meeting, no folder,
    no way to tell which of several hundred rows is at fault. That is exactly
    what a real crash looked like, and diagnosing it needed a database dump.
    """

    def __init__(self, recording_id: int, folder: str, owner: int | None) -> None:
        super().__init__(
            f"folder {folder!r} is already used by recording {owner}, "
            f"so recording {recording_id} cannot also have it"
        )
        self.recording_id = recording_id
        self.folder = folder
        self.owner = owner


class Database:
    """Every query in one place, so no caller writes SQL of its own."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.db_path()
        self._lock = threading.Lock()
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._migrate()

    def connect(self) -> sqlite3.Connection:
        """One connection per thread.

        sqlite3 objects are not shareable across threads, and the web server
        reads on handler threads while the sync writes on its own.
        """
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
            # Tracked so close() can reach connections opened on other threads.
            # Without this the web server's handler threads keep the file open
            # and the database cannot be replaced or deleted on Windows.
            with self._lock:
                self._all.append(conn)
        return conn

    def close(self) -> None:
        """Close every connection this Database opened, on any thread."""
        with self._lock:
            for conn in self._all:
                with contextlib.suppress(sqlite3.Error):
                    conn.close()
            self._all.clear()
        self._local.conn = None

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        with self._lock, conn:
            yield conn

    # -- schema ---------------------------------------------------------- #

    def _migrate(self) -> None:
        conn = self.connect()
        with self._lock, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

                CREATE TABLE IF NOT EXISTS meetings (
                    recording_id      INTEGER PRIMARY KEY,
                    title             TEXT NOT NULL DEFAULT '',
                    meeting_title     TEXT NOT NULL DEFAULT '',
                    started_at        TEXT NOT NULL DEFAULT '',
                    created_at        TEXT NOT NULL DEFAULT '',
                    folder            TEXT NOT NULL DEFAULT '',
                    attendees_json    TEXT NOT NULL DEFAULT '[]',
                    recorded_by       TEXT NOT NULL DEFAULT '',
                    share_url         TEXT NOT NULL DEFAULT '',
                    url               TEXT NOT NULL DEFAULT '',
                    content_hash      TEXT NOT NULL DEFAULT '',
                    fetched_at        TEXT NOT NULL DEFAULT '',
                    has_transcript    INTEGER NOT NULL DEFAULT 0,
                    has_summary       INTEGER NOT NULL DEFAULT 0,
                    action_item_count INTEGER NOT NULL DEFAULT 0,
                    transcript_chars  INTEGER NOT NULL DEFAULT 0,
                    media_state       TEXT NOT NULL DEFAULT 'none'
                );
                CREATE INDEX IF NOT EXISTS meetings_started
                    ON meetings(started_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS meetings_folder
                    ON meetings(folder) WHERE folder <> '';

                CREATE TABLE IF NOT EXISTS runs (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at    TEXT NOT NULL,
                    finished_at   TEXT,
                    status        TEXT NOT NULL DEFAULT 'running',
                    requests_made INTEGER NOT NULL DEFAULT 0,
                    meetings_seen INTEGER NOT NULL DEFAULT 0,
                    written       INTEGER NOT NULL DEFAULT 0,
                    skipped       INTEGER NOT NULL DEFAULT 0,
                    errors        INTEGER NOT NULL DEFAULT 0,
                    cancelled     INTEGER NOT NULL DEFAULT 0,
                    options_json  TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS run_events (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id  INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    ts      TEXT NOT NULL,
                    level   TEXT NOT NULL DEFAULT 'info',
                    message TEXT NOT NULL DEFAULT '',
                    data_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS run_events_run ON run_events(run_id, id);
                """
            )
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))

        self._ensure_fts()

    def _ensure_fts(self) -> None:
        global _fts_available
        conn = self.connect()
        try:
            with self._lock, conn:
                conn.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
                        speaker, timestamp UNINDEXED, text,
                        recording_id UNINDEXED, tokenize='porter unicode61'
                    );
                    """
                )
            _fts_available = True
        except sqlite3.OperationalError as exc:
            _fts_available = False
            log.warning("FTS5 unavailable, search will fall back to LIKE: %s", exc)

    @property
    def fts_available(self) -> bool:
        return bool(_fts_available)

    # -- meetings -------------------------------------------------------- #

    def upsert_meeting(self, row: MeetingRow) -> None:
        try:
            self._upsert(row)
        except sqlite3.IntegrityError as exc:
            if "meetings.folder" not in str(exc):
                raise
            raise FolderTaken(row.recording_id, row.folder, self.folder_owner(row.folder)) from exc

    def _upsert(self, row: MeetingRow) -> None:
        with self.write() as conn:
            conn.execute(
                """
                INSERT INTO meetings (
                    recording_id, title, meeting_title, started_at, created_at, folder,
                    attendees_json, recorded_by, share_url, url, content_hash, fetched_at,
                    has_transcript, has_summary, action_item_count, transcript_chars,
                    media_state
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(recording_id) DO UPDATE SET
                    title=excluded.title,
                    meeting_title=excluded.meeting_title,
                    started_at=excluded.started_at,
                    created_at=excluded.created_at,
                    folder=excluded.folder,
                    attendees_json=excluded.attendees_json,
                    recorded_by=excluded.recorded_by,
                    share_url=excluded.share_url,
                    url=excluded.url,
                    content_hash=excluded.content_hash,
                    fetched_at=excluded.fetched_at,
                    has_transcript=excluded.has_transcript,
                    has_summary=excluded.has_summary,
                    action_item_count=excluded.action_item_count,
                    transcript_chars=excluded.transcript_chars,
                    media_state=excluded.media_state
                """,
                (
                    row.recording_id,
                    row.title,
                    row.meeting_title,
                    row.started_at,
                    row.created_at,
                    row.folder,
                    json.dumps(row.attendees, ensure_ascii=False),
                    row.recorded_by,
                    row.share_url,
                    row.url,
                    row.content_hash,
                    row.fetched_at,
                    int(row.has_transcript),
                    int(row.has_summary),
                    row.action_item_count,
                    row.transcript_chars,
                    row.media_state,
                ),
            )

    def get_meeting(self, recording_id: int) -> MeetingRow | None:
        row = (
            self.connect()
            .execute("SELECT * FROM meetings WHERE recording_id = ?", (recording_id,))
            .fetchone()
        )
        return _row_to_meeting(row) if row else None

    def content_hash(self, recording_id: int) -> str:
        row = (
            self.connect()
            .execute("SELECT content_hash FROM meetings WHERE recording_id = ?", (recording_id,))
            .fetchone()
        )
        return (row["content_hash"] if row else "") or ""

    def folder_owner(self, folder: str) -> int | None:
        """Which recording already owns this folder name, if any.

        The collision check. Two identical recurring meetings on one day produce
        the same name, and without this the second silently overwrites the first.
        """
        row = (
            self.connect()
            .execute("SELECT recording_id FROM meetings WHERE folder = ?", (folder,))
            .fetchone()
        )
        return int(row["recording_id"]) if row else None

    def release_folder(self, recording_id: int) -> None:
        """Give up a recording's claim on its folder name.

        Used when the folder on disk demonstrably belongs to someone else. The
        row survives with an empty folder, which the partial unique index
        ignores, so the rightful owner can take the name.
        """
        with self.write() as conn:
            conn.execute("UPDATE meetings SET folder = '' WHERE recording_id = ?", (recording_id,))

    def count_meetings(self) -> int:
        row = self.connect().execute("SELECT COUNT(*) AS n FROM meetings").fetchone()
        return int(row["n"]) if row else 0

    def list_meetings(
        self,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
        order: str = "started_at",
        descending: bool = True,
    ) -> tuple[list[MeetingRow], int]:
        """A page of meetings plus the total matching count."""
        # Allow-list the sort column: it is interpolated, and anything reaching
        # here from a query string must not be able to become SQL.
        columns = {
            "started_at",
            "title",
            "recording_id",
            "fetched_at",
            "action_item_count",
            "transcript_chars",
        }
        column = order if order in columns else "started_at"
        direction = "DESC" if descending else "ASC"

        where, params = "", []
        if query.strip():
            where = "WHERE title LIKE ? OR meeting_title LIKE ? OR attendees_json LIKE ?"
            like = f"%{query.strip()}%"
            params = [like, like, like]

        conn = self.connect()
        total_row = conn.execute(f"SELECT COUNT(*) AS n FROM meetings {where}", params).fetchone()
        total = int(total_row["n"]) if total_row else 0
        rows = conn.execute(
            f"SELECT * FROM meetings {where} ORDER BY {column} {direction} LIMIT ? OFFSET ?",
            [*params, max(1, limit), max(0, offset)],
        ).fetchall()
        return [_row_to_meeting(r) for r in rows], total

    def latest_started_at(self) -> str:
        row = self.connect().execute("SELECT MAX(started_at) AS latest FROM meetings").fetchone()
        return (row["latest"] if row else "") or ""

    # -- search ---------------------------------------------------------- #

    def index_transcript(self, recording_id: int, items: list[dict[str, Any]]) -> None:
        if not self.fts_available:
            return
        with self.write() as conn:
            conn.execute("DELETE FROM transcripts_fts WHERE recording_id = ?", (recording_id,))
            conn.executemany(
                "INSERT INTO transcripts_fts (speaker, timestamp, text, recording_id)"
                " VALUES (?,?,?,?)",
                [
                    (
                        ((item or {}).get("speaker") or {}).get("display_name") or "",
                        (item or {}).get("timestamp") or "",
                        (item or {}).get("text") or "",
                        recording_id,
                    )
                    for item in items
                    if ((item or {}).get("text") or "").strip()
                ],
            )

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Find utterances matching a query.

        Two strategies, because one row here is one spoken line. FTS5 treats a
        bare multi-word query as "all of these words in the same row", so a
        search for `commercial pipeline` matches nothing when those two words
        are never in one short utterance -- even in meetings about exactly that.

          * A single word, or a "quoted phrase": matched directly, and a phrase
            keeps its adjacency requirement.
          * Several bare words: meetings where *every* word appears somewhere,
            returning the lines that match any of them, best first.

        Per-utterance rows are kept either way, because that is what gives a
        result its speaker and timestamp.
        """
        cleaned = query.strip()
        if not cleaned:
            return []

        words = [w for w in re.findall(r"[^\W_]+", cleaned) if len(w) > 1]
        quoted = '"' in cleaned

        # A quoted phrase means "these words, adjacent", which is tier one's job.
        # Anything else with several words goes straight to tier two rather than
        # using it as a fallback: tier two's meetings are a superset, so a single
        # lucky same-line hit would otherwise hide every meeting where the words
        # are spread across the conversation -- which is most of them.
        if quoted or len(words) < 2 or not self.fts_available:
            return self._match(cleaned, limit)

        conn = self.connect()
        try:
            shared: set[int] | None = None
            for word in words:
                rows = conn.execute(
                    "SELECT DISTINCT recording_id FROM transcripts_fts"
                    " WHERE transcripts_fts MATCH ?",
                    (f'"{word}"',),
                ).fetchall()
                found = {int(r["recording_id"]) for r in rows}
                shared = found if shared is None else (shared & found)
                if not shared:
                    return []

            if not shared:
                return []

            placeholders = ",".join("?" * len(shared))
            any_word = " OR ".join(f'"{w}"' for w in words)
            rows = conn.execute(
                f"""
                SELECT f.recording_id AS recording_id, f.speaker AS speaker,
                       f.timestamp AS timestamp,
                       snippet(transcripts_fts, 2, '[', ']', ' ... ', 12) AS snippet,
                       m.title AS title, m.started_at AS started_at, m.folder AS folder
                FROM transcripts_fts f
                LEFT JOIN meetings m ON m.recording_id = f.recording_id
                WHERE transcripts_fts MATCH ? AND f.recording_id IN ({placeholders})
                ORDER BY rank LIMIT ?
                """,
                (any_word, *sorted(shared), max(1, limit)),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            log.info("second-tier search rejected: %s", exc)
            return []
        return [dict(r) for r in rows]

    def _match(self, query: str, limit: int) -> list[dict[str, Any]]:
        """One FTS query, with a LIKE fallback for a Python built without FTS5."""
        conn = self.connect()
        if self.fts_available:
            try:
                rows = conn.execute(
                    """
                    SELECT f.recording_id AS recording_id, f.speaker AS speaker,
                           f.timestamp AS timestamp,
                           snippet(transcripts_fts, 2, '[', ']', ' ... ', 12) AS snippet,
                           m.title AS title, m.started_at AS started_at, m.folder AS folder
                    FROM transcripts_fts f
                    LEFT JOIN meetings m ON m.recording_id = f.recording_id
                    WHERE transcripts_fts MATCH ?
                    ORDER BY rank LIMIT ?
                    """,
                    (query, max(1, limit)),
                ).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError as exc:
                # A bare MATCH syntax error (an unbalanced quote, a lone NOT)
                # must not 500 the UI; fall through to LIKE.
                log.info("FTS query rejected, falling back to LIKE: %s", exc)

        rows = conn.execute(
            """
            SELECT f.recording_id AS recording_id, f.speaker AS speaker,
                   f.timestamp AS timestamp, f.text AS snippet,
                   m.title AS title, m.started_at AS started_at, m.folder AS folder
            FROM transcripts_fts f
            LEFT JOIN meetings m ON m.recording_id = f.recording_id
            WHERE f.text LIKE ? LIMIT ?
            """,
            (f"%{query}%", max(1, limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- runs ------------------------------------------------------------ #

    def start_run(self, options: dict[str, Any]) -> int:
        with self.write() as conn:
            cursor = conn.execute(
                "INSERT INTO runs (started_at, status, options_json) VALUES (?,?,?)",
                (utc_now(), "running", json.dumps(options, ensure_ascii=False)),
            )
            return int(cursor.lastrowid or 0)

    def finish_run(self, run_id: int, status: str, **counts: int) -> None:
        fields = ", ".join(f"{k} = ?" for k in counts)
        setters = f"finished_at = ?, status = ?{', ' + fields if fields else ''}"
        with self.write() as conn:
            conn.execute(
                f"UPDATE runs SET {setters} WHERE id = ?",
                [utc_now(), status, *counts.values(), run_id],
            )

    def add_event(
        self, run_id: int, message: str, level: str = "info", data: dict[str, Any] | None = None
    ) -> None:
        with self.write() as conn:
            conn.execute(
                "INSERT INTO run_events (run_id, ts, level, message, data_json) VALUES (?,?,?,?,?)",
                (run_id, utc_now(), level, message, json.dumps(data or {}, ensure_ascii=False)),
            )

    def events_since(
        self, run_id: int, after_id: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        rows = (
            self.connect()
            .execute(
                "SELECT id, ts, level, message, data_json FROM run_events"
                " WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?",
                (run_id, after_id, max(1, limit)),
            )
            .fetchall()
        )
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["data"] = json.loads(item.pop("data_json") or "{}")
            except ValueError:
                item["data"] = {}
            out.append(item)
        return out

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        row = self.connect().execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def last_run(self) -> dict[str, Any] | None:
        row = self.connect().execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def active_run(self) -> dict[str, Any] | None:
        row = (
            self.connect()
            .execute("SELECT * FROM runs WHERE status = 'running' ORDER BY id DESC LIMIT 1")
            .fetchone()
        )
        return dict(row) if row else None

    def mark_stale_runs(self) -> int:
        """Close runs left 'running' by a process that died.

        Without this a crashed backfill leaves a row that makes the UI report a
        sync in progress forever, and the guard against concurrent runs then
        refuses to start a new one.
        """
        with self.write() as conn:
            cursor = conn.execute(
                "UPDATE runs SET status = 'interrupted', finished_at = ? WHERE status = 'running'",
                (utc_now(),),
            )
            return cursor.rowcount or 0


_shared: Database | None = None


def shared() -> Database:
    global _shared
    if _shared is None:
        _shared = Database()
    return _shared


def reset_shared() -> None:
    global _shared
    if _shared is not None:
        _shared.close()
    _shared = None
