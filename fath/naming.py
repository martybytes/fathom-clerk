"""Turning a meeting into a folder name.

The rules encode seven defects documented in docs/naming.md. Each fix is
commented where it lives. Do not rewrite them from this docstring.

The charset rule everything else serves: output matches ^[a-z0-9_-]+$ and
nothing else. No spaces, no case, no ASCII punctuation beyond _ and -, no
non-ASCII. That is safe unquoted on Windows, macOS and Linux, safe in a URL, and
safe for an agent to paste into a shell without escaping. `_` separates the three
segments and `-` separates words inside a segment, so the two roles never
overlap and split("_") recovers date, title and attendees.

Pure functions and one frozen value object, no I/O. NamingRules can be built from
saved settings or from a dict of proposed ones, which is what lets the web UI
preview a naming change without saving it first.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from fath import config

SAFE_NAME = re.compile(r"^[a-z0-9_-]+$")

WINDOWS_MAX_PATH = 260  # counts the terminating null, so 259 is the usable maximum
# MAX_PATH counts that null, and a later artifact with a longer suffix should not
# silently push existing folders over the edge.
PATH_SAFETY_MARGIN = 8
LONGEST_FILE_SUFFIX = "_action-items.md"

# Windows device names. A folder called `con` cannot be created at all.
RESERVED_STEMS = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{i}" for i in range(1, 10)]
    + [f"lpt{i}" for i in range(1, 10)]
)


@dataclass(frozen=True)
class NamingRules:
    date_format: str = "%Y-%m-%d"
    date_source: str = "recording_start_time"
    title_source: str = "title"
    timezone_mode: str = "local"
    template: str = "{date}_{title}_{attendees}"
    segment_sep: str = "_"
    word_sep: str = "-"
    max_attendees: int = 5
    overflow_suffix: str = "-and-{n}-more"
    collision_suffix: str = "-{recording_id}"
    exclude_patterns: tuple[str, ...] = ()
    exclude_recorder: bool = False
    dedupe_first_names: bool = True
    strip_names_from_title: bool = True
    title_replacements: dict[str, str] = field(default_factory=dict)
    edge_stopwords: frozenset[str] = frozenset()
    max_name_len: int = 120
    min_name_len: int = 40

    @classmethod
    def from_config(cls, cfg: config.Config | None = None) -> NamingRules:
        conf = cfg or config.shared()
        return cls(
            date_format=str(conf.get("naming.dateFormat")),
            date_source=str(conf.get("naming.dateSource")),
            title_source=str(conf.get("naming.titleSource")),
            timezone_mode=str(conf.get("naming.timezone")),
            template=str(conf.get("naming.folderTemplate")),
            segment_sep=str(conf.get("naming.segmentSeparator")),
            word_sep=str(conf.get("naming.wordSeparator")),
            max_attendees=conf.get_int("naming.maxAttendees", 5),
            overflow_suffix=str(conf.get("naming.overflowSuffix")),
            collision_suffix=str(conf.get("naming.collisionSuffix")),
            exclude_patterns=tuple(str(p) for p in conf.get_list("naming.excludeAttendeePatterns")),
            exclude_recorder=conf.get_bool("naming.excludeRecorder"),
            dedupe_first_names=conf.get_bool("naming.dedupeFirstNames", True),
            strip_names_from_title=conf.get_bool("naming.stripAttendeeNamesFromTitle", True),
            title_replacements=conf.get_dict("naming.titleReplacements"),
            edge_stopwords=frozenset(str(conf.get("naming.edgeStopwords", "")).split()),
            max_name_len=conf.get_int("naming.maxFolderNameLen", 120),
            min_name_len=conf.get_int("naming.minFolderNameLen", 40),
        )

    @classmethod
    def from_overrides(
        cls, overrides: dict[str, Any], cfg: config.Config | None = None
    ) -> NamingRules:
        """Saved settings with some replaced. Powers the web UI's live preview.

        Keys are the dotted setting names, so the preview endpoint can pass the
        form straight through without a second naming vocabulary.
        """
        base = cls.from_config(cfg)
        mapping = {
            "naming.dateFormat": "date_format",
            "naming.dateSource": "date_source",
            "naming.titleSource": "title_source",
            "naming.timezone": "timezone_mode",
            "naming.folderTemplate": "template",
            "naming.segmentSeparator": "segment_sep",
            "naming.wordSeparator": "word_sep",
            "naming.maxAttendees": "max_attendees",
            "naming.overflowSuffix": "overflow_suffix",
            "naming.collisionSuffix": "collision_suffix",
            "naming.excludeRecorder": "exclude_recorder",
            "naming.dedupeFirstNames": "dedupe_first_names",
            "naming.stripAttendeeNamesFromTitle": "strip_names_from_title",
            "naming.maxFolderNameLen": "max_name_len",
            "naming.minFolderNameLen": "min_name_len",
        }
        changes: dict[str, Any] = {}
        for key, attr in mapping.items():
            if key in overrides:
                changes[attr] = overrides[key]
        if "naming.excludeAttendeePatterns" in overrides:
            changes["exclude_patterns"] = tuple(overrides["naming.excludeAttendeePatterns"])
        if "naming.titleReplacements" in overrides:
            changes["title_replacements"] = dict(overrides["naming.titleReplacements"])
        if "naming.edgeStopwords" in overrides:
            changes["edge_stopwords"] = frozenset(str(overrides["naming.edgeStopwords"]).split())
        return dataclass_replace(base, changes)


def dataclass_replace(rules: NamingRules, changes: dict[str, Any]) -> NamingRules:
    import dataclasses

    return dataclasses.replace(rules, **changes) if changes else rules


def slug(raw: str, word_sep: str = "-") -> str:
    """Lowercase ASCII, separator for everything else.

    An allow-list, not a block-list. Anything outside [a-z0-9] becomes the word
    separator, so the Windows reserved set, the macOS colon, the Linux slash and
    every shell metacharacter are handled without enumerating any of them -- and
    so is whatever turns up in a meeting title next month.
    """
    text = unicodedata.normalize("NFKD", raw)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", word_sep, text)
    return text.strip(word_sep)


def first_name(full_name: str, word_sep: str = "-") -> str:
    """The first whitespace-delimited token.

    Splitting on whitespace rather than commas is what makes "Casey Example, Ph.D."
    yield "casey" instead of "casey fowler" plus a stray " ph.d." field.

    An invitee with no display name arrives as a bare email address, and slugging
    that whole string gives "parker-example-labs-com" -- twenty characters of domain
    for one person. Two of those in one meeting overflowed MAX_PATH on the first
    full run. The local part before the @ is the name.
    """
    stripped = full_name.strip()
    if not stripped:
        return ""
    if "@" in stripped:
        local = stripped.split("@", 1)[0]
        # first.last@, first_last@ and first+tag@ all mean the same first name.
        return slug(re.split(r"[._+-]", local)[0], word_sep)
    return slug(stripped.split()[0], word_sep)


def is_real_person(name: str, patterns: tuple[str, ...]) -> bool:
    lowered = name.strip().lower()
    if not lowered:
        return False
    return not any(fnmatch(lowered, pattern.lower()) for pattern in patterns)


def attendee_names(meeting: dict[str, Any], rules: NamingRules) -> list[str]:
    """Full names of the human invitees, rooms and resource accounts removed."""
    names = []
    for invitee in meeting.get("calendar_invitees") or []:
        name = (invitee or {}).get("name") or ""
        if is_real_person(name, rules.exclude_patterns):
            names.append(name)
    if rules.exclude_recorder:
        recorder = ((meeting.get("recorded_by") or {}).get("name") or "").strip().lower()
        names = [n for n in names if n.strip().lower() != recorder]
    return names


def attendee_segment(names: list[str], rules: NamingRules, max_count: int | None = None) -> str:
    limit = rules.max_attendees if max_count is None else max_count
    firsts = [first_name(n, rules.word_sep) for n in names]
    firsts = [f for f in firsts if f]
    if rules.dedupe_first_names:
        firsts = sorted(set(firsts))
    else:
        firsts = sorted(firsts)
    if not firsts:
        return ""
    if len(firsts) <= limit:
        return rules.word_sep.join(firsts)
    kept = firsts[:limit]
    return rules.word_sep.join(kept) + rules.overflow_suffix.format(n=len(firsts) - limit)


def name_tokens(meeting: dict[str, Any], names: list[str], rules: NamingRules) -> set[str]:
    """Every word that is somebody's name, for stripping out of the title.

    Includes the recorder even when exclude_recorder is false. "Jules/Marley R&D
    Updates" is recorded by Jules and invites only Marley, so building this set
    from the invitee list alone leaves "jules" in the title -- exactly the
    redundancy the third segment already covers.
    """
    tokens = set()
    recorder = (meeting.get("recorded_by") or {}).get("name") or ""
    for name in [*names, recorder]:
        for part in name.replace(",", " ").split():
            token = slug(part, rules.word_sep)
            if len(token) > 1:
                tokens.add(token)
    return tokens


def title_segment(meeting: dict[str, Any], names: list[str], rules: NamingRules) -> str:
    primary = meeting.get(rules.title_source) or ""
    fallback = meeting.get("meeting_title" if rules.title_source == "title" else "title") or ""
    raw = (primary or fallback or "").strip()
    if not raw:
        return "untitled"

    # Before punctuation is stripped: the only moment at which "1:1" and "&"
    # still carry meaning. After the allow-list pass "1:1" would just be "1-1".
    for needle, replacement in rules.title_replacements.items():
        raw = raw.replace(needle, replacement)

    text = slug(raw, rules.word_sep)
    if rules.strip_names_from_title and text:
        # "Jules & Marley 1:1" -> "1-on-1".
        drop = name_tokens(meeting, names, rules)
        kept = [w for w in text.split(rules.word_sep) if w and w not in drop]
        # Once the names go, the "and" that came from "&" is orphaned debris.
        # Edges only: "example for consulting" has to keep its middle "for".
        while kept and kept[0] in rules.edge_stopwords:
            kept.pop(0)
        while kept and kept[-1] in rules.edge_stopwords:
            kept.pop()
        if kept:
            text = rules.word_sep.join(kept)
    return text or "untitled"


def meeting_datetime(meeting: dict[str, Any], rules: NamingRules) -> datetime:
    """Start time, in local time unless configured otherwise.

    The API answers in UTC. Formatting that directly files a 7pm Central meeting
    under the following day, which nobody notices until they go looking for a
    meeting that is not where they left it.
    """
    order = [rules.date_source, "recording_start_time", "scheduled_start_time", "created_at"]
    for field_name in dict.fromkeys(order):
        raw = meeting.get(field_name)
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (
            parsed.astimezone(timezone.utc) if rules.timezone_mode == "utc" else parsed.astimezone()
        )
    return datetime.now().astimezone()


def max_name_len(out_root: Path | None, rules: NamingRules) -> int:
    """The longest folder name this output root can hold.

    Derived, not a constant, because the name appears *twice* in the deepest
    path: once as the folder and once as the filename prefix. A flat 120-char cap
    produced a 292-character path under an ordinary Documents folder, which
    Windows rejects. Solving

        260 >= len(root) + 1 + N + 1 + N + len(suffix)

    for N is the only version of this that stays correct when the output root
    moves somewhere deeper.
    """
    if out_root is None:
        return rules.max_name_len
    budget = (
        WINDOWS_MAX_PATH - len(str(out_root)) - 2 - len(LONGEST_FILE_SUFFIX) - PATH_SAFETY_MARGIN
    )
    return max(rules.min_name_len, min(rules.max_name_len, budget // 2))


def _trim_title(title: str, excess: int, rules: NamingRules) -> str:
    """Shorten a title segment by roughly `excess` characters, on a word boundary."""
    trimmed = title[: max(8, len(title) - excess)].rstrip(rules.word_sep)
    # Slicing on a character count alone produced
    # "l10-cadence-example-traders-sample-sol" and
    # "commercial-r-and-d-pipeline-meet", where the severed word reads as a
    # different word rather than as an abbreviation.
    if rules.word_sep in trimmed:
        whole_words = trimmed.rsplit(rules.word_sep, 1)[0]
        if len(whole_words) >= 8:
            trimmed = whole_words
    # A cut that lands on "...migration-of" reads as truncated debris.
    words = trimmed.split(rules.word_sep)
    while len(words) > 1 and words[-1] in rules.edge_stopwords:
        words.pop()
    return rules.word_sep.join(words) or "untitled"


def build_folder_name(
    meeting: dict[str, Any],
    rules: NamingRules,
    out_root: Path | None = None,
    collision_suffix: str = "",
) -> str:
    """The folder name, including any disambiguating suffix.

    The suffix is an argument rather than something the caller appends, because
    appending it afterwards can push a long name past MAX_PATH: the folder is
    created (short enough on its own) and the write inside it then fails with a
    bare FileNotFoundError naming a path that looked perfectly fine. The length
    cap has to see the final name.
    """
    names = attendee_names(meeting, rules)
    date_part = meeting_datetime(meeting, rules).strftime(rules.date_format)
    title = title_segment(meeting, names, rules)
    cap = max_name_len(out_root, rules)

    def assemble(title_text: str, attendee_count: int) -> str:
        rendered = rules.template.format(
            date=date_part,
            title=title_text,
            attendees=attendee_segment(names, rules, attendee_count),
        )
        # An empty attendee segment must not leave a trailing separator behind.
        parts = [p for p in rendered.split(rules.segment_sep) if p]
        return rules.segment_sep.join(parts) + collision_suffix

    name = assemble(title, rules.max_attendees)

    # Shed the title first, since it is the part with redundancy in it. Only once
    # the title is at its floor does the attendee list start giving ground: a
    # meeting whose invitees are bare email addresses can carry twenty characters
    # of domain per person, which no amount of title trimming will offset.
    attendee_count = rules.max_attendees
    if len(name) > cap:
        title = _trim_title(title, len(name) - cap, rules)
        name = assemble(title, attendee_count)
    while len(name) > cap and attendee_count > 1:
        attendee_count -= 1
        name = assemble(title, attendee_count)

    if any(segment in RESERVED_STEMS for segment in name.split(rules.segment_sep)):
        # Prefixed, not replaced, and not with "_" -- that would corrupt the
        # segment split that makes the name parseable.
        name = f"x-{name}"

    if not SAFE_NAME.match(name):
        # Without this a path that escaped the allow-list would surface as a
        # mangled filename somewhere far away instead.
        raise ValueError(f"unsafe folder name produced: {name!r}")
    return name


def collision_suffix_for(rules: NamingRules, recording_id: int) -> str:
    return rules.collision_suffix.format(recording_id=recording_id)
