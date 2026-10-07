"""Turning a meeting payload into the files in its folder.

Markdown carries YAML front matter so an agent reading one file out of context
still knows what meeting it came from, who was there and where to find the
recording. That is the whole point of the folder layout: a transcript that has
been copied somewhere else is still self-describing.

Which files get written is driven entirely by `files.*` settings, so turning off
an artifact costs nothing and turning it back on needs a re-sync with
`sync.overwriteExisting`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fath import config
from fath.naming import NamingRules, meeting_datetime

# Written to the folder even when meetingJson is off, because without it a
# rebuild cannot tell which recording a folder belongs to and would have to
# re-download everything. Tiny, and it is not the full payload.
STAMP_NAME = ".fath.json"


@dataclass(frozen=True)
class FileSelection:
    transcript: bool = True
    summary: bool = True
    action_items: bool = True
    meeting_json: bool = True
    highlights: bool = False
    media: bool = False
    prefix_with_folder: bool = True

    @classmethod
    def from_config(cls, cfg: config.Config | None = None) -> FileSelection:
        conf = cfg or config.shared()
        return cls(
            transcript=conf.get_bool("files.transcript", True),
            summary=conf.get_bool("files.summary", True),
            action_items=conf.get_bool("files.actionItems", True),
            meeting_json=conf.get_bool("files.meetingJson", True),
            highlights=conf.get_bool("files.highlights", False),
            media=conf.get_bool("files.media", False),
            prefix_with_folder=conf.get_bool("files.prefixWithFolderName", True),
        )

    @classmethod
    def from_overrides(
        cls, overrides: dict[str, Any], cfg: config.Config | None = None
    ) -> FileSelection:
        import dataclasses

        base = cls.from_config(cfg)
        mapping = {
            "files.transcript": "transcript",
            "files.summary": "summary",
            "files.actionItems": "action_items",
            "files.meetingJson": "meeting_json",
            "files.highlights": "highlights",
            "files.media": "media",
            "files.prefixWithFolderName": "prefix_with_folder",
        }
        changes = {attr: bool(overrides[key]) for key, attr in mapping.items() if key in overrides}
        return dataclasses.replace(base, **changes) if changes else base

    def any_text(self) -> bool:
        return any(
            (self.transcript, self.summary, self.action_items, self.meeting_json, self.highlights)
        )


def content_hash(meeting: dict[str, Any]) -> str:
    """A stable digest of the parts that would change what gets written.

    Deliberately not the whole payload: Fathom returns a fresh signed share_url
    and CRM block on every call, so hashing everything would mark every meeting
    changed on every run and rewrite unchanged folders nightly for no reason.
    """
    material = {
        "title": meeting.get("title"),
        "meeting_title": meeting.get("meeting_title"),
        "started": meeting.get("recording_start_time"),
        "invitees": [(i or {}).get("name") for i in (meeting.get("calendar_invitees") or [])],
        "transcript_len": len(meeting.get("transcript") or []),
        "summary": ((meeting.get("default_summary") or {}) or {}).get("markdown_formatted"),
        "actions": [(a or {}).get("description") for a in (meeting.get("action_items") or [])],
        "highlights": len(meeting.get("highlights") or []),
    }
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def attendee_display_names(meeting: dict[str, Any]) -> list[str]:
    return [
        (i or {}).get("name") or ""
        for i in (meeting.get("calendar_invitees") or [])
        if (i or {}).get("name")
    ]


def front_matter(meeting: dict[str, Any], rules: NamingRules) -> str:
    started = meeting_datetime(meeting, rules)
    recorder = (meeting.get("recorded_by") or {}).get("name") or ""
    lines = [
        "---",
        f"title: {json.dumps(meeting.get('title') or '')}",
        f"meeting_title: {json.dumps(meeting.get('meeting_title') or '')}",
        f"date: {started.strftime('%Y-%m-%d')}",
        f"start_time: {started.isoformat()}",
        f"recording_id: {meeting.get('recording_id')}",
        f"url: {meeting.get('url') or ''}",
        f"share_url: {meeting.get('share_url') or ''}",
        f"recorded_by: {json.dumps(recorder)}",
        f"attendees: {json.dumps(attendee_display_names(meeting), ensure_ascii=False)}",
        "---",
        "",
    ]
    return "\n".join(lines)


def render_transcript(meeting: dict[str, Any], rules: NamingRules) -> str:
    items = meeting.get("transcript") or []
    out = [front_matter(meeting, rules), "# Transcript", ""]
    if not items:
        out.append("_No transcript available for this meeting._")
        out.append("")
    for item in items:
        speaker = ((item or {}).get("speaker") or {}).get("display_name") or "Unknown"
        stamp = (item or {}).get("timestamp") or ""
        text = ((item or {}).get("text") or "").strip()
        if text:
            out.append(f"**[{stamp}] {speaker}:** {text}")
            out.append("")
    return "\n".join(out)


def render_summary(meeting: dict[str, Any], rules: NamingRules) -> str:
    summary = meeting.get("default_summary") or {}
    body = summary.get("markdown_formatted") or "_No summary available._"
    template = summary.get("template_name") or "unknown"
    return "\n".join([front_matter(meeting, rules), f"# Summary ({template})", "", body, ""])


def render_action_items(meeting: dict[str, Any], rules: NamingRules) -> str:
    items = meeting.get("action_items") or []
    out = [front_matter(meeting, rules), "# Action items", ""]
    if not items:
        out.append("_None recorded._")
    for item in items:
        done = "x" if (item or {}).get("completed") else " "
        who = ((item or {}).get("assignee") or {}).get("name") or "unassigned"
        stamp = (item or {}).get("recording_timestamp") or ""
        link = (item or {}).get("recording_playback_url") or ""
        text = ((item or {}).get("description") or "").strip()
        suffix = f" ([{stamp}]({link}))" if link else ""
        out.append(f"- [{done}] **{who}** - {text}{suffix}")
    out.append("")
    return "\n".join(out)


def render_highlights(meeting: dict[str, Any], rules: NamingRules) -> str:
    items = meeting.get("highlights") or []
    out = [front_matter(meeting, rules), "# Highlights", ""]
    if not items:
        out.append("_None recorded._")
    for item in items:
        label = (item or {}).get("type") or "highlight"
        summary = (item or {}).get("summary") or ""
        text = (item or {}).get("text") or ""
        start = (item or {}).get("start_time")
        body = summary or text
        stamp = f" ({start}s)" if start is not None else ""
        out.append(f"- **{label}**{stamp} - {body}")
    out.append("")
    return "\n".join(out)


def plan_files(
    meeting: dict[str, Any], folder_name: str, rules: NamingRules, selection: FileSelection
) -> dict[str, str]:
    """Filename -> contents, for everything enabled."""
    stem = f"{folder_name}_" if selection.prefix_with_folder else ""
    files: dict[str, str] = {}
    if selection.transcript:
        files[f"{stem}transcript.md"] = render_transcript(meeting, rules)
    if selection.summary:
        files[f"{stem}summary.md"] = render_summary(meeting, rules)
    if selection.action_items:
        files[f"{stem}action-items.md"] = render_action_items(meeting, rules)
    if selection.highlights:
        files[f"{stem}highlights.md"] = render_highlights(meeting, rules)
    if selection.meeting_json:
        files[f"{stem}meeting.json"] = json.dumps(meeting, indent=2, ensure_ascii=False)
    return files


def write_meeting(
    folder: Path,
    meeting: dict[str, Any],
    folder_name: str,
    rules: NamingRules,
    selection: FileSelection,
    max_path: int = 260,
) -> list[Path]:
    """Write the folder. Raises with a readable message if a path is too long."""
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for filename, body in plan_files(meeting, folder_name, rules, selection).items():
        target = folder / filename
        resolved = len(str(target.resolve()))
        if resolved >= max_path:
            # Windows raises a bare FileNotFoundError here, which names the file
            # but not the reason and reads as a missing directory.
            raise OSError(
                f"path too long ({resolved} >= {max_path}): {target}\n"
                f"lower naming.maxFolderNameLen or choose a shallower output folder"
            )
        target.write_text(body, encoding="utf-8")
        written.append(target)

    # The stamp always goes down, even when meeting.json is off, so a rebuild
    # from disk can still identify the folder without any API calls.
    stamp = folder / STAMP_NAME
    stamp.write_text(
        json.dumps(
            {
                "recording_id": meeting.get("recording_id"),
                "content_hash": content_hash(meeting),
                "title": meeting.get("title") or "",
                "started_at": meeting_datetime(meeting, rules).isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return written
