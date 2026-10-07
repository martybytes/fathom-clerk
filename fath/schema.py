"""What every setting IS: type, allowed values, default, group, and which layer won.

Declared once, here. The CLI's `fath config`, the Textual settings screen and the
web app's settings page all render from this list, so a setting added once shows
up in all three with its label, its help text and its validation intact. Without
a single declaration the three drift, and the one that drifts is always the one
you are not looking at.

Each field also shows **which layer won**, so a value that is right for the
wrong reason is visible rather than merely correct today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fath import config

# Flags
RESTART = "restart"  # takes effect on the next sync, not immediately
ADVANCED = "advanced"  # hidden behind a disclosure in the UIs
DERIVED = "derived"  # shown read-only; computed, not stored

KIND_BOOL = "bool"
KIND_INT = "int"
KIND_FLOAT = "float"
KIND_TEXT = "text"
KIND_CHOICE = "choice"
KIND_LIST = "list"
KIND_MAP = "map"
KIND_PATH = "path"


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    kind: str
    group: str
    note: str = ""
    options: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    flags: frozenset[str] = field(default_factory=frozenset)

    def default(self) -> Any:
        node: Any = config.DEFAULTS
        for part in self.key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def validate(self, value: Any) -> str | None:
        """None when acceptable, else why not -- phrased for a user, not a log."""
        if self.kind == KIND_BOOL:
            if not isinstance(value, bool):
                return "must be true or false"
            return None

        if self.kind in (KIND_INT, KIND_FLOAT):
            try:
                number = int(value) if self.kind == KIND_INT else float(value)
            except (TypeError, ValueError):
                return "must be a number"
            if self.minimum is not None and number < self.minimum:
                return f"must be at least {self.minimum:g}"
            if self.maximum is not None and number > self.maximum:
                return f"must be at most {self.maximum:g}"
            return None

        if self.kind == KIND_CHOICE:
            if str(value) not in self.options:
                return f"must be one of: {', '.join(self.options)}"
            return None

        if self.kind == KIND_LIST:
            if not isinstance(value, list):
                return "must be a list"
            return None

        if self.kind == KIND_MAP:
            if not isinstance(value, dict):
                return "must be a set of key/value pairs"
            return None

        if self.kind in (KIND_TEXT, KIND_PATH):
            if not isinstance(value, str):
                return "must be text"
            return None

        return None

    def coerce(self, value: Any) -> Any:
        """Turn a JSON or form value into the type this setting stores.

        A checkbox arrives as the string "true" and a number field as "5"; storing
        those verbatim would make get_bool and get_int quietly do the wrong thing
        later, far from here.
        """
        if self.kind == KIND_BOOL:
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)
        if self.kind == KIND_INT:
            return int(value)
        if self.kind == KIND_FLOAT:
            return float(value)
        if self.kind in (KIND_TEXT, KIND_PATH, KIND_CHOICE):
            return str(value)
        return value


SETTINGS: tuple[Setting, ...] = (
    # -- where things go ------------------------------------------------- #
    Setting(
        "outputRoot",
        "Output folder",
        KIND_PATH,
        "Locations",
        note="Where meeting folders are written. Empty means Documents\\Fathom. "
        "Changing this does not move what is already on disk.",
    ),
    # -- what to download ------------------------------------------------ #
    Setting(
        "files.transcript",
        "Transcript (.md)",
        KIND_BOOL,
        "Downloads",
        note="The words themselves. This is what full-text search indexes. Free: it "
        "arrives with the meeting list at no extra request.",
    ),
    Setting(
        "files.summary",
        "Summary (.md)",
        KIND_BOOL,
        "Downloads",
        note="Fathom's AI summary. The fastest way to recall a meeting. Free.",
    ),
    Setting(
        "files.actionItems",
        "Action items (.md)",
        KIND_BOOL,
        "Downloads",
        note="Commitments with assignee, timestamp and a playback link. Free.",
    ),
    Setting(
        "files.meetingJson",
        "Meeting data (.json)",
        KIND_BOOL,
        "Downloads",
        note="The full API payload. Free, and worth keeping: it is what lets the "
        "database be rebuilt from disk without re-downloading anything. Turn it "
        "off and a rebuild costs a full re-sync instead of a folder scan.",
    ),
    Setting(
        "files.highlights",
        "Highlights (.md)",
        KIND_BOOL,
        "Downloads",
        note="Only useful if you bookmark moments during calls. Free.",
    ),
    Setting(
        "files.media",
        "Video / audio",
        KIND_BOOL,
        "Downloads",
        note="The recording itself. Expensive: roughly 150 MB and two extra API "
        "requests per meeting, against a limit of 10 requests per minute. For a "
        "full history that is tens of gigabytes and hours of waiting.",
        flags=frozenset({RESTART}),
    ),
    Setting(
        "files.prefixWithFolderName",
        "Prefix filenames with the folder name",
        KIND_BOOL,
        "Downloads",
        note="Writes 2024-04-19_weekly-demo-review_blaire-jules_transcript.md rather than "
        "transcript.md. Verbose, but a file stays identifiable after an agent "
        "copies it somewhere else.",
    ),
    # -- naming ---------------------------------------------------------- #
    Setting(
        "naming.folderTemplate",
        "Folder template",
        KIND_TEXT,
        "Naming",
        note="Placeholders: {date}, {title}, {attendees}. Underscores separate the "
        "three parts; dashes separate words inside a part, so the name can be "
        "split back apart.",
    ),
    Setting(
        "naming.dateFormat",
        "Date format",
        KIND_TEXT,
        "Naming",
        note="strftime format. %Y-%m-%d is ISO 8601 and sorts correctly as text.",
    ),
    Setting(
        "naming.dateSource",
        "Date taken from",
        KIND_CHOICE,
        "Naming",
        options=("recording_start_time", "scheduled_start_time", "created_at"),
        note="When the meeting happened, when it was scheduled, or when Fathom created the record.",
    ),
    Setting(
        "naming.titleSource",
        "Title taken from",
        KIND_CHOICE,
        "Naming",
        options=("title", "meeting_title"),
        note="Fathom's title, or the original calendar event title.",
    ),
    Setting(
        "naming.timezone",
        "Timezone",
        KIND_CHOICE,
        "Naming",
        options=("local", "utc"),
        note="The API answers in UTC. Using UTC files an evening meeting under the next day.",
    ),
    Setting(
        "naming.maxAttendees",
        "Maximum attendees in the name",
        KIND_INT,
        "Naming",
        minimum=1,
        maximum=20,
        note="Beyond this the rest are summarised as -and-N-more.",
    ),
    Setting(
        "naming.excludeRecorder",
        "Leave the recorder out",
        KIND_BOOL,
        "Naming",
        note="You record most meetings, so your own name appears in nearly every "
        "folder. Turning this on shortens names but drops you from the list.",
    ),
    Setting(
        "naming.dedupeFirstNames",
        "Merge duplicate first names",
        KIND_BOOL,
        "Naming",
        note="Two attendees called Erin become one 'erin' rather than 'erin-erin'.",
    ),
    Setting(
        "naming.stripAttendeeNamesFromTitle",
        "Remove attendee names from the title",
        KIND_BOOL,
        "Naming",
        note="'Jules & Marley 1:1' becomes '1-on-1', since the names are already the "
        "third part of the folder name.",
    ),
    Setting(
        "naming.excludeAttendeePatterns",
        "Ignore these attendees",
        KIND_LIST,
        "Naming",
        note="Wildcards allowed. Filters meeting rooms and shared mailboxes that "
        "appear in the invitee list as though they were people.",
    ),
    Setting(
        "naming.titleReplacements",
        "Title replacements",
        KIND_MAP,
        "Naming",
        note="Applied before punctuation is stripped, which is the only point at "
        "which '1:1' and '&' still mean anything.",
        flags=frozenset({ADVANCED}),
    ),
    Setting(
        "naming.edgeStopwords",
        "Trim these words from the ends",
        KIND_TEXT,
        "Naming",
        note="Space-separated. Stops a title reading '...migration-of' after it has "
        "been shortened.",
        flags=frozenset({ADVANCED}),
    ),
    Setting(
        "naming.maxFolderNameLen",
        "Folder name limit",
        KIND_INT,
        "Naming",
        minimum=40,
        maximum=200,
        note="A ceiling, not the operative number. The real limit is worked out from "
        "your output folder, because Windows caps a full path at 260 characters "
        "and the name appears twice in it.",
        flags=frozenset({ADVANCED}),
    ),
    Setting(
        "naming.overflowSuffix",
        "Overflow suffix",
        KIND_TEXT,
        "Naming",
        note="{n} is the number of attendees left out.",
        flags=frozenset({ADVANCED}),
    ),
    Setting(
        "naming.collisionSuffix",
        "Collision suffix",
        KIND_TEXT,
        "Naming",
        note="Appended when two meetings would produce the same folder name. "
        "{recording_id} is unique and stable.",
        flags=frozenset({ADVANCED}),
    ),
    # -- sync ------------------------------------------------------------ #
    Setting(
        "sync.backfillSince",
        "Only fetch meetings after",
        KIND_TEXT,
        "Sync",
        note="A date like 2026-01-01. Empty means everything, back to your first recording.",
    ),
    Setting(
        "sync.overwriteExisting",
        "Rewrite meetings already on disk",
        KIND_BOOL,
        "Sync",
        note="Off, an unchanged meeting is skipped without writing. Turn on after "
        "changing the naming rules or the file selection.",
    ),
    Setting(
        "sync.reimportBeforeSync",
        "Adopt folders already on disk first",
        KIND_BOOL,
        "Sync",
        note="Scans the output folder and reads each meeting.json to rebuild the "
        "database. Costs no API requests, and is what stops a reinstall "
        "re-downloading everything.",
    ),
    Setting(
        "sync.recordedBy",
        "Only these recorders",
        KIND_LIST,
        "Sync",
        note="Email addresses. Empty means every meeting your key can see.",
        flags=frozenset({ADVANCED}),
    ),
    # -- api -------------------------------------------------------------- #
    Setting(
        "api.minIntervalSec",
        "Minimum seconds between requests",
        KIND_FLOAT,
        "Rate limiting",
        minimum=0.0,
        maximum=60.0,
        note="A floor only. The real pace is read from Fathom's own rate-limit "
        "headers, which report 10 requests per minute.",
    ),
    Setting(
        "api.reserveRequests",
        "Requests held in reserve",
        KIND_INT,
        "Rate limiting",
        minimum=0,
        maximum=9,
        note="Stop and wait for the window to reset with this many requests still "
        "unused, so a retry always has room.",
    ),
    Setting(
        "api.maxRetries",
        "Retries after a failure",
        KIND_INT,
        "Rate limiting",
        minimum=0,
        maximum=10,
        note="Applies to rate limits, server errors and dropped connections.",
    ),
    Setting(
        "api.maxBackoffSec",
        "Longest wait between retries",
        KIND_INT,
        "Rate limiting",
        minimum=1,
        maximum=600,
        flags=frozenset({ADVANCED}),
    ),
    Setting(
        "api.timeoutSec",
        "Request timeout",
        KIND_INT,
        "Rate limiting",
        minimum=5,
        maximum=300,
        flags=frozenset({ADVANCED}),
    ),
    # -- web -------------------------------------------------------------- #
    Setting(
        "web.port",
        "Web dashboard port",
        KIND_INT,
        "Web",
        minimum=1024,
        maximum=65535,
        note="Bound to 127.0.0.1 only, never the network.",
        flags=frozenset({RESTART}),
    ),
    Setting(
        "web.openBrowser",
        "Open the browser automatically",
        KIND_BOOL,
        "Web",
    ),
    Setting(
        "log.level",
        "Log detail",
        KIND_CHOICE,
        "Web",
        options=("DEBUG", "INFO", "WARNING", "ERROR"),
        note="DEBUG records every rate-limit header, which is how you verify pacing.",
        flags=frozenset({ADVANCED}),
    ),
)

BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}

GROUPS: tuple[str, ...] = tuple(dict.fromkeys(s.group for s in SETTINGS))


def get(key: str) -> Setting | None:
    return BY_KEY.get(key)


def describe(key: str, cfg: config.Config | None = None) -> dict[str, Any]:
    """One setting as the UIs render it: value, default, source, and metadata."""
    setting = BY_KEY[key]
    conf = cfg or config.shared()
    return {
        "key": setting.key,
        "label": setting.label,
        "kind": setting.kind,
        "group": setting.group,
        "note": setting.note,
        "options": list(setting.options),
        "min": setting.minimum,
        "max": setting.maximum,
        "flags": sorted(setting.flags),
        "value": conf.get(setting.key),
        "default": setting.default(),
        "source": conf.source_of(setting.key),
    }


def snapshot(cfg: config.Config | None = None) -> list[dict[str, Any]]:
    """Every setting, in declaration order. The read model the UIs consume."""
    conf = cfg or config.shared()
    return [describe(s.key, conf) for s in SETTINGS]


def validate_many(values: dict[str, Any]) -> dict[str, str]:
    """Return {key: reason} for everything that would be rejected.

    All of them, not the first: a settings form that reports one error at a time
    makes the user submit five times to find five mistakes.
    """
    errors: dict[str, str] = {}
    for key, value in values.items():
        setting = BY_KEY.get(key)
        if setting is None:
            errors[key] = "not a known setting"
            continue
        try:
            coerced = setting.coerce(value)
        except (TypeError, ValueError):
            errors[key] = f"could not be read as {setting.kind}"
            continue
        problem = setting.validate(coerced)
        if problem:
            errors[key] = problem
    return errors


def coerce_many(values: dict[str, Any]) -> dict[str, Any]:
    return {key: BY_KEY[key].coerce(value) for key, value in values.items() if key in BY_KEY}
