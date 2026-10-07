import { useEffect, useState } from "react";
import { api, type Meeting, type MeetingDetail } from "../lib/api";
import { Button, Empty, Icon, Panel, Spinner } from "../components/ui";
import emptyMeetings from "../assets/empty-meetings.webp";

/** The YAML block is for agents reading the file, not for a reader in the app. */
function stripFrontMatter(text: string): string {
  if (!text.startsWith("---")) return text;
  const end = text.indexOf("\n---", 3);
  return end === -1 ? text : text.slice(end + 4).trimStart();
}

export default function Meetings() {
  const [rows, setRows] = useState<Meeting[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<MeetingDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<"transcript" | "summary" | "actionItems">("summary");

  useEffect(() => {
    // Debounced: a request per keystroke would hammer SQLite for no benefit.
    const timer = setTimeout(() => {
      setLoading(true);
      api
        .meetings(query, 200)
        .then((result) => {
          setRows(result.items);
          setTotal(result.total);
        })
        .finally(() => setLoading(false));
    }, 180);
    return () => clearTimeout(timer);
  }, [query]);

  function open(id: number) {
    api.meeting(id).then((detail) => {
      setSelected(detail);
      setTab(detail.summary ? "summary" : "transcript");
    });
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[26rem_1fr]">
      <Panel
        title={`Meetings (${total})`}
        icon="event_note"
        actions={loading ? <Spinner /> : null}
      >
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="filter by title or attendee..."
          className="mb-3 w-full rounded-md border border-surface0 bg-crust px-3 py-1.5 text-sm outline-none focus:border-blue"
        />
        <div className="max-h-[34rem] space-y-1 overflow-y-auto">
          {rows.length === 0 && !loading && !query ? (
            <Empty
              image={emptyMeetings}
              title="No meetings yet"
              hint="Run a sync, or adopt folders you already have with fath init --import."
            />
          ) : rows.length === 0 && !loading ? (
            <Empty icon="search_off" title="No meetings match" />
          ) : (
            rows.map((row) => (
              <button
                key={row.recording_id}
                type="button"
                onClick={() => open(row.recording_id)}
                className={`w-full rounded-md px-2.5 py-2 text-left transition hover:bg-surface0/60 ${
                  selected?.recording_id === row.recording_id ? "bg-surface0" : ""
                }`}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-sm font-medium">
                    {row.title || "untitled"}
                  </span>
                  <span className="shrink-0 font-mono text-[11px] text-overlay">
                    {row.started_at.slice(0, 10)}
                  </span>
                </div>
                <div className="truncate text-xs text-overlay">
                  {row.attendees.slice(0, 4).join(", ")}
                  {row.attendees.length > 4 ? ` +${row.attendees.length - 4}` : ""}
                </div>
              </button>
            ))
          )}
        </div>
      </Panel>

      {selected ? (
        <Panel
          title={selected.title || "untitled"}
          icon="description"
          actions={
            selected.share_url ? (
              <a
                href={selected.share_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-blue hover:underline"
              >
                <Icon name="open_in_new" className="text-[14px]" />
                open in Fathom
              </a>
            ) : null
          }
        >
          <div className="mb-3 space-y-1 text-xs text-overlay">
            <div className="break-all font-mono">{selected.folderPath}</div>
            <div>
              {selected.attendees.join(", ")} &middot; recorded by {selected.recorded_by}
            </div>
          </div>

          <div className="mb-3 flex gap-1">
            {(
              [
                ["summary", "Summary"],
                ["transcript", "Transcript"],
                ["actionItems", `Action items (${selected.action_item_count})`],
              ] as const
            ).map(([id, label]) => (
              <Button
                key={id}
                variant={tab === id ? "primary" : "ghost"}
                onClick={() => setTab(id)}
              >
                {label}
              </Button>
            ))}
          </div>

          <pre className="max-h-[30rem] overflow-auto whitespace-pre-wrap rounded-md bg-crust p-3 font-mono text-xs leading-relaxed text-subtext">
            {stripFrontMatter(selected[tab]) || "(nothing written for this meeting)"}
          </pre>
        </Panel>
      ) : (
        <Panel>
          <Empty
            icon="touch_app"
            title="Pick a meeting"
            hint="Its summary, transcript and action items are read straight off disk."
          />
        </Panel>
      )}
    </div>
  );
}
