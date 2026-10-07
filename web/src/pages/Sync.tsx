import { useEffect, useRef, useState } from "react";
import { api, type ProgressEvent, type RateSnapshot, type Status } from "../lib/api";
import { Button, Empty, Icon, Panel, Spinner } from "../components/ui";
import syncIdle from "../assets/sync-done.webp";

// Each artifact, with its real cost. Everything except media arrives inline with
// the meeting list, so turning it on costs nothing extra -- the UI says so
// rather than leaving the user to guess which choices are expensive.
const ARTIFACTS = [
  {
    key: "files.transcript",
    label: "Transcript",
    file: "_transcript.md",
    cost: "free",
    why: "The words themselves. This is what full-text search indexes.",
  },
  {
    key: "files.summary",
    label: "Summary",
    file: "_summary.md",
    cost: "free",
    why: "Fathom's AI summary. The fastest way to recall what a meeting was about.",
  },
  {
    key: "files.actionItems",
    label: "Action items",
    file: "_action-items.md",
    cost: "free",
    why: "Commitments with assignee, timestamp and a playback link.",
  },
  {
    key: "files.meetingJson",
    label: "Meeting data",
    file: "_meeting.json",
    cost: "free",
    why: "The full API payload. Keep it: it is what lets the database be rebuilt from disk without re-downloading anything.",
    drawback:
      "Turn this off and rebuilding costs a full re-sync instead of a folder scan.",
  },
  {
    key: "files.highlights",
    label: "Highlights",
    file: "_highlights.md",
    cost: "free",
    why: "Only useful if you bookmark moments during calls.",
  },
] as const;

const PRESETS: Record<string, Record<string, boolean>> = {
  Everything: {
    "files.transcript": true,
    "files.summary": true,
    "files.actionItems": true,
    "files.meetingJson": true,
    "files.highlights": true,
  },
  "Markdown only": {
    "files.transcript": true,
    "files.summary": true,
    "files.actionItems": true,
    "files.meetingJson": false,
    "files.highlights": true,
  },
  "Transcripts only": {
    "files.transcript": true,
    "files.summary": false,
    "files.actionItems": false,
    "files.meetingJson": false,
    "files.highlights": false,
  },
};

function estimateMedia(meetings: number): string {
  const gb = Math.round((meetings * 150) / 1024);
  const minutes = Math.round((meetings * 2) / 10);
  const time =
    minutes > 90 ? `about ${Math.round(minutes / 60)} hours` : `about ${minutes} minutes`;
  return `roughly ${gb} GB and ${time}`;
}

export function RateMeter({ rate }: { rate: RateSnapshot | null }) {
  const limit = rate?.limit ?? 10;
  const remaining = rate?.remaining ?? limit;
  const pct = Math.max(0, Math.min(100, (remaining / limit) * 100));
  const tone = remaining <= 2 ? "bg-red" : remaining <= 4 ? "bg-yellow" : "bg-green";
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-overlay">requests left this minute</span>
        <span className="font-mono">
          {remaining} / {limit}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface0">
        <div className={`h-full ${tone} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <div className="flex justify-between text-[11px] text-overlay">
        <span>{rate?.resetSec != null ? `resets in ${rate.resetSec}s` : " "}</span>
        <span>{rate?.paceSec ? `pacing ${rate.paceSec.toFixed(1)}s apart` : " "}</span>
      </div>
    </div>
  );
}

export default function SyncPage({
  status,
  onChanged,
  notify,
}: {
  status: Status | null;
  onChanged: () => void;
  notify: (message: string, tone?: "ok" | "error") => void;
}) {
  const [files, setFiles] = useState<Record<string, boolean>>({});
  const [media, setMedia] = useState(false);
  const [dryRun, setDryRun] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [rate, setRate] = useState<RateSnapshot | null>(null);
  const [running, setRunning] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    api
      .settings()
      .then(({ settings }) => {
        const current: Record<string, boolean> = {};
        for (const setting of settings) {
          if (setting.key.startsWith("files.")) current[setting.key] = Boolean(setting.value);
        }
        setFiles(current);
        setMedia(Boolean(current["files.media"]));
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (status?.running && !running) attach(status.activeRun?.id ?? 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.running]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [events]);

  useEffect(() => () => sourceRef.current?.close(), []);

  function attach(runId: number) {
    sourceRef.current?.close();
    setRunning(true);
    // Server-sent events, not a socket: the progress stream is one-way, and the
    // server replays from the database so a reload loses nothing.
    const source = new EventSource(`/api/sync/stream?run_id=${runId}`);
    sourceRef.current = source;
    source.addEventListener("progress", (raw) => {
      const item = JSON.parse((raw as MessageEvent).data) as ProgressEvent;
      setEvents((previous) => [...previous.slice(-800), item]);
      if (item.data?.rate) setRate(item.data.rate);
    });
    source.addEventListener("done", () => {
      source.close();
      setRunning(false);
      onChanged();
    });
    source.onerror = () => {
      source.close();
      setRunning(false);
    };
  }

  async function start() {
    setEvents([]);
    try {
      const { runId } = await api.startSync({
        dryRun,
        overwrite,
        files: { ...files, "files.media": media },
      });
      attach(runId);
    } catch (exception) {
      notify(String((exception as Error).message), "error");
    }
  }

  const meetings = status?.meetings ?? 0;
  const counts = [...events].reverse().find((e) => e.data?.counts)?.data.counts;

  return (
    <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
      <div className="space-y-4">
        <Panel title="What to download" icon="download">
          <div className="mb-3 flex flex-wrap gap-1.5">
            {Object.keys(PRESETS).map((name) => (
              <Button key={name} variant="ghost" onClick={() => setFiles(PRESETS[name])}>
                {name}
              </Button>
            ))}
          </div>

          <div className="space-y-2">
            {ARTIFACTS.map((artifact) => (
              <label
                key={artifact.key}
                className="flex cursor-pointer gap-2.5 rounded-md p-2 hover:bg-surface0/40"
              >
                <input
                  type="checkbox"
                  checked={Boolean(files[artifact.key])}
                  onChange={(event) =>
                    setFiles({ ...files, [artifact.key]: event.target.checked })
                  }
                  className="mt-0.5 accent-blue"
                />
                <span className="flex-1">
                  <span className="flex items-baseline justify-between">
                    <span className="text-sm font-medium">{artifact.label}</span>
                    <span className="text-[11px] text-green">{artifact.cost}</span>
                  </span>
                  <span className="block font-mono text-[11px] text-overlay">
                    {artifact.file}
                  </span>
                  <span className="mt-0.5 block text-xs leading-snug text-subtext">
                    {artifact.why}
                  </span>
                  {"drawback" in artifact && artifact.drawback && !files[artifact.key] && (
                    <span className="mt-1 block text-xs leading-snug text-yellow">
                      {artifact.drawback}
                    </span>
                  )}
                </span>
              </label>
            ))}

            <label className="flex cursor-pointer gap-2.5 rounded-md border border-peach/30 bg-peach/5 p-2">
              <input
                type="checkbox"
                checked={media}
                onChange={(event) => setMedia(event.target.checked)}
                className="mt-0.5 accent-peach"
              />
              <span className="flex-1">
                <span className="flex items-baseline justify-between">
                  <span className="text-sm font-medium">Video / audio</span>
                  <span className="text-[11px] text-peach">expensive</span>
                </span>
                <span className="mt-0.5 block text-xs leading-snug text-subtext">
                  The recording itself: about 150 MB and two extra API requests each.
                </span>
                {media && meetings > 0 && (
                  <span className="mt-1 block text-xs font-medium leading-snug text-peach">
                    For your {meetings} meetings that is {estimateMedia(meetings)}.
                  </span>
                )}
              </span>
            </label>
          </div>
        </Panel>

        <Panel title="How this run behaves" icon="tune">
          <label className="flex cursor-pointer items-start gap-2.5 py-1.5">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(event) => setDryRun(event.target.checked)}
              className="mt-0.5 accent-blue"
            />
            <span>
              <span className="text-sm">Dry run</span>
              <span className="block text-xs text-overlay">
                Show the folder names, write nothing.
              </span>
            </span>
          </label>
          <label className="flex cursor-pointer items-start gap-2.5 py-1.5">
            <input
              type="checkbox"
              checked={overwrite}
              onChange={(event) => setOverwrite(event.target.checked)}
              className="mt-0.5 accent-blue"
            />
            <span>
              <span className="text-sm">Rewrite unchanged meetings</span>
              <span className="block text-xs text-overlay">
                Normally an unchanged meeting is skipped without writing. Turn this on
                after changing the naming rules or the file selection.
              </span>
            </span>
          </label>

          <div className="mt-3 flex gap-2">
            <Button variant="primary" icon="sync" onClick={start} disabled={running}>
              {running ? "Syncing..." : dryRun ? "Preview" : "Sync now"}
            </Button>
            <Button
              variant="danger"
              icon="stop_circle"
              disabled={!running}
              onClick={() => api.cancelSync().then(() => notify("cancelling..."))}
            >
              Cancel
            </Button>
          </div>
        </Panel>
      </div>

      <div className="space-y-4">
        <Panel title="Rate limiting" icon="speed">
          <p className="text-sm leading-relaxed text-subtext">
            Fathom allows <strong className="text-text">10 API requests per minute</strong>.
            This tool reads the limit from every response and paces itself to stay under
            it, so a sync gets slower rather than failing. One request returns about ten
            meetings <em>with their transcripts</em>, so all {meetings || "your"} meetings
            cost roughly {Math.max(1, Math.ceil(meetings / 10))} requests &mdash; a few
            minutes. Day-to-day runs only fetch what is new, usually one or two requests.
          </p>
          <p className="mt-2 text-sm leading-relaxed text-subtext">
            Downloading video is the expensive case: two requests per meeting, and the
            limit is what makes that take hours rather than minutes.
          </p>
          <div className="mt-3">
            <RateMeter rate={rate} />
          </div>
        </Panel>

        <Panel
          title="Progress"
          icon="terminal"
          actions={
            <div className="flex items-center gap-3 text-xs text-overlay">
              {running && <Spinner />}
              {counts && (
                <span className="font-mono">
                  {counts.written} written &middot; {counts.skipped} unchanged &middot;{" "}
                  {counts.requests} requests
                </span>
              )}
            </div>
          }
        >
          <div
            ref={logRef}
            className="h-[26rem] overflow-y-auto rounded-md bg-crust p-3 font-mono text-xs leading-relaxed"
          >
            {events.length === 0 ? (
              <Empty
                image={syncIdle}
                title="Nothing running"
                hint="Pick what you want and press Sync. Progress appears here line by line."
              />
            ) : (
              events.map((event) => (
                <div
                  key={event.id}
                  className={
                    event.level === "error"
                      ? "text-red"
                      : event.level === "warning"
                        ? "text-yellow"
                        : event.data?.kind === "skipped"
                          ? "text-overlay"
                          : event.data?.kind === "meeting"
                            ? "text-green"
                            : "text-subtext"
                  }
                >
                  {event.data?.kind === "meeting" && (
                    <Icon name="check" className="mr-1 text-[13px] align-[-2px]" />
                  )}
                  {event.data?.folder || event.message}
                </div>
              ))
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}
