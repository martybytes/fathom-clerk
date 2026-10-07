"""The terminal dashboard.

**This screen never writes settings itself.** It calls the same config.set() the
CLI calls, and renders from the same fath/schema.py. Two writers is how a
settings file loses edits the other writer never saw, and tests/test_ui.py
enforces the rule rather than trusting it.

The sync runs on a worker thread and the screen consumes the same SyncEvent
stream the CLI prints, so progress here and progress there cannot disagree.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    RichLog,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from fath import config, db, keystore, naming, paths, schema, sync

# Textual identifiers allow only letters, numbers, underscores and hyphens, so a
# dotted setting key cannot be used as a widget id verbatim. Encode rather than
# keep a parallel lookup table: the id stays reversible and there is nothing to
# fall out of sync.
ID_PREFIX = "set-"
DOT = "__"


def key_to_id(key: str) -> str:
    return ID_PREFIX + key.replace(".", DOT)


def id_to_key(widget_id: str | None) -> str:
    if not widget_id or not widget_id.startswith(ID_PREFIX):
        return ""
    return widget_id[len(ID_PREFIX) :].replace(DOT, ".")


SOURCE_CLASS = {
    "config.json": "source-config",
    "local.json": "source-local",
    "default": "source-default",
}


class FathApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "fathom-helper"

    # ClassVar because Textual reads BINDINGS off the class, not the instance.
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("s", "start_sync", "Sync"),
        Binding("c", "cancel_sync", "Cancel"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.cfg = config.shared()
        self.db = db.shared()
        self._cancel = False
        self._running = False

    # -- layout ---------------------------------------------------------- #

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="home"):
            with TabPane("Home", id="home"):
                yield Static(id="overview", classes="panel")
                with Horizontal(id="actions"):
                    yield Button("Sync now", id="sync", variant="primary")
                    yield Button("Dry run", id="dry")
                    yield Button("Cancel", id="cancel")
                yield RichLog(id="log", markup=True, wrap=True, highlight=False)
            with TabPane("Meetings", id="meetings"):
                yield Input(placeholder="filter by title or attendee...", id="filter")
                yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            with TabPane("Search", id="search"):
                yield Input(placeholder="search every transcript...", id="query")
                yield DataTable(id="hits", cursor_type="row", zebra_stripes=True)
            with TabPane("Naming", id="naming"):
                yield Static(
                    "Change a rule and the preview updates against your real meetings.",
                    classes="muted",
                )
                yield Input(placeholder="max attendees (1-20)", id="max-attendees")
                yield Static(id="preview")
            with TabPane("Settings", id="settings"):
                yield VerticalScroll(id="settings-body")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns("date", "folder", "attendees", "chars")
        hits = self.query_one("#hits", DataTable)
        hits.add_columns("date", "meeting", "at", "said")
        self.refresh_all()
        self.build_settings()

    # -- reads ------------------------------------------------------------ #

    def action_refresh(self) -> None:
        self.refresh_all()

    def refresh_all(self) -> None:
        self.refresh_overview()
        self.refresh_meetings()
        self.refresh_preview()

    def refresh_overview(self) -> None:
        key = keystore.describe()
        root, source = paths.resolution()
        last = self.db.last_run()
        out_root = self.cfg.output_root()

        key_line = (
            f"[#a6e3a1]set[/] from {key['source']} (...{key['tail']})"
            if key["set"]
            else "[#f38ba8]not set[/] - run `fath key --set`"
        )
        run_line = (
            "never"
            if last is None
            else (
                f"#{last['id']} {last['status']} - {last['written']} written, "
                f"{last['skipped']} unchanged, {last['requests_made']} requests"
            )
        )
        lines = [
            "[b #cba6f7]fathom-helper[/]",
            "",
            f"[#a6adc8]meetings   [/] {self.db.count_meetings()}",
            f"[#a6adc8]output     [/] {out_root}",
            f"[#a6adc8]settings   [/] {root} [#6c7086](from {source})[/]",
            f"[#a6adc8]api key    [/] {key_line}",
            f"[#a6adc8]last run   [/] {run_line}",
            "",
            "[#6c7086]Fathom allows 10 API requests a minute. One request brings back "
            "about 10 meetings with their transcripts, so a full history is roughly "
            "one request per ten meetings and a routine run is one or two.[/]",
        ]
        self.query_one("#overview", Static).update("\n".join(lines))

    def refresh_meetings(self, query: str = "") -> None:
        table = self.query_one("#table", DataTable)
        table.clear()
        rows, _ = self.db.list_meetings(query=query, limit=500)
        for row in rows:
            table.add_row(
                row.started_at[:10],
                row.folder,
                ", ".join(row.attendees[:3]),
                f"{row.transcript_chars:,}",
                key=str(row.recording_id),
            )

    def refresh_preview(self) -> None:
        raw = self.query_one("#max-attendees", Input).value.strip()
        overrides: dict[str, Any] = {}
        if raw.isdigit():
            overrides["naming.maxAttendees"] = max(1, min(20, int(raw)))

        rules = naming.NamingRules.from_overrides(overrides, self.cfg)
        out_root = self.cfg.output_root()
        rows, _ = self.db.list_meetings(limit=12)

        lines = ["[b]folder names under these rules[/]", ""]
        for row in rows:
            meeting = self._meeting_payload(row.recording_id, out_root, row)
            try:
                lines.append(naming.build_folder_name(meeting, rules, out_root))
            except ValueError as exc:
                lines.append(f"[#f38ba8]{exc}[/]")
        if not rows:
            lines.append("[#6c7086]nothing synced yet - run a sync first[/]")
        self.query_one("#preview", Static).update("\n".join(lines))

    def _meeting_payload(self, recording_id: int, out_root: Path, row: Any) -> dict[str, Any]:
        """The stored payload if it is on disk, else enough of one to rename."""
        folder = out_root / row.folder
        if folder.is_dir():
            found = next(folder.glob("*meeting.json"), None)
            if found is not None:
                try:
                    return json.loads(found.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
        return {
            "recording_id": recording_id,
            "title": row.title,
            "meeting_title": row.meeting_title,
            "recording_start_time": row.started_at,
            "recorded_by": {"name": row.recorded_by},
            "calendar_invitees": [{"name": n} for n in row.attendees],
        }

    # -- settings --------------------------------------------------------- #

    def build_settings(self) -> None:
        body = self.query_one("#settings-body", VerticalScroll)
        for group in schema.GROUPS:
            body.mount(Static(group, classes="panel-title"))
            for setting in schema.SETTINGS:
                if setting.group != group or schema.ADVANCED in setting.flags:
                    continue
                body.mount(self._setting_row(setting))

    def _setting_row(self, setting: schema.Setting) -> Vertical:
        described = schema.describe(setting.key, self.cfg)
        source = str(described["source"])
        widgets: list[Any] = [
            Static(
                f"{setting.label}  [{SOURCE_CLASS[source]}]({source})[/]",
                markup=True,
            )
        ]

        if setting.kind == schema.KIND_BOOL:
            widgets.append(Switch(value=bool(described["value"]), id=key_to_id(setting.key)))
        elif setting.kind == schema.KIND_CHOICE:
            widgets.append(
                Select(
                    [(o, o) for o in setting.options],
                    value=str(described["value"]),
                    id=key_to_id(setting.key),
                    allow_blank=False,
                )
            )
        else:
            value = described["value"]
            text = json.dumps(value) if isinstance(value, (list, dict)) else str(value or "")
            widgets.append(Input(value=text, id=key_to_id(setting.key)))

        if setting.note:
            widgets.append(Static(setting.note, classes="setting-note"))
        return Vertical(*widgets, classes="setting-row")

    # -- saving: one writer, the same one the CLI uses -------------------- #

    def _save(self, key: str, raw: Any) -> None:
        """Validate, coerce, persist, and say what happened.

        Deliberately routed through schema.validate_many and config.set() rather
        than writing JSON here: the CLI, this screen and the web app must not be
        three different ideas of what a valid setting is.
        """
        setting = schema.get(key)
        if setting is None:
            return
        value: Any = raw
        if setting.kind in (schema.KIND_LIST, schema.KIND_MAP) and isinstance(raw, str):
            try:
                value = json.loads(raw)
            except ValueError:
                self.notify(f"{setting.label}: needs to be valid JSON", severity="error")
                return

        errors = schema.validate_many({key: value})
        if errors:
            self.notify(f"{setting.label}: {errors[key]}", severity="error")
            return

        self.cfg.set(key, setting.coerce(value))
        self.notify(f"{setting.label} saved")
        if key.startswith("naming.") or key == "outputRoot":
            self.refresh_preview()
        self.refresh_overview()

    @on(Switch.Changed)
    def _switch_saved(self, event: Switch.Changed) -> None:
        key = id_to_key(event.switch.id)
        if key:
            self._save(key, event.value)

    @on(Select.Changed)
    def _select_saved(self, event: Select.Changed) -> None:
        key = id_to_key(event.select.id)
        if key:
            self._save(key, event.value)

    @on(Input.Submitted)
    def _input_saved(self, event: Input.Submitted) -> None:
        key = id_to_key(event.input.id)
        if key:
            self._save(key, event.value)

    # -- events ----------------------------------------------------------- #

    @on(Input.Changed, "#filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        self.refresh_meetings(event.value)

    @on(Input.Changed, "#max-attendees")
    def _preview_changed(self) -> None:
        self.refresh_preview()

    @on(Input.Submitted, "#query")
    def _search(self, event: Input.Submitted) -> None:
        hits = self.query_one("#hits", DataTable)
        hits.clear()
        for hit in self.db.search(event.value, limit=100):
            hits.add_row(
                str(hit.get("started_at") or "")[:10],
                str(hit.get("title") or ""),
                str(hit.get("timestamp") or ""),
                str(hit.get("snippet") or "").strip().replace("\n", " ")[:90],
            )

    @on(Button.Pressed, "#sync")
    def _sync_pressed(self) -> None:
        self.action_start_sync()

    @on(Button.Pressed, "#dry")
    def _dry_pressed(self) -> None:
        self.action_start_sync(dry_run=True)

    @on(Button.Pressed, "#cancel")
    def _cancel_pressed(self) -> None:
        self.action_cancel_sync()

    def action_cancel_sync(self) -> None:
        if self._running:
            self._cancel = True
            self.query_one("#log", RichLog).write("[#f9e2af]cancelling after this meeting...[/]")

    def action_start_sync(self, dry_run: bool = False) -> None:
        if self._running:
            self.query_one("#log", RichLog).write("[#f9e2af]a sync is already running[/]")
            return
        self._cancel = False
        self._running = True
        self.run_sync(dry_run)

    @work(thread=True)
    def run_sync(self, dry_run: bool) -> None:
        """On a worker thread: a backfill is minutes long and must not block the UI."""
        log = self.query_one("#log", RichLog)
        options = sync.SyncOptions.from_config(self.cfg, dry_run=dry_run)
        try:
            for event in sync.run_to_database(options, self.cfg, lambda: self._cancel):
                self.call_from_thread(self._write_event, log, event)
        finally:
            self._running = False
            self.call_from_thread(self.refresh_overview)
            self.call_from_thread(self.refresh_meetings)

    def _write_event(self, log: RichLog, event: sync.SyncEvent) -> None:
        colour = {"error": "#f38ba8", "warning": "#f9e2af"}.get(event.level, "#cdd6f4")
        if event.kind == sync.MEETING:
            rate = ""
            if event.rate and event.rate.remaining is not None:
                rate = f" [#6c7086]({event.rate.remaining} requests left)[/]"
            log.write(f"[#a6e3a1]+[/] {event.folder}{rate}")
        elif event.kind == sync.SKIPPED:
            log.write(f"[#6c7086]= {event.message}[/]")
        else:
            log.write(f"[{colour}]{event.message}[/]")


def main(argv: list[str] | None = None) -> int:
    FathApp().run()
    return 0
