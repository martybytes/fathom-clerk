import { useEffect, useState } from "react";
import { api, type Check, type Status } from "../lib/api";
import { Button, Field, Icon, Panel, Stat } from "../components/ui";
import hero from "../assets/hero.webp";

function relative(iso: string | null | undefined): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export default function Dashboard({
  status,
  onChanged,
  notify,
  goTo,
}: {
  status: Status | null;
  onChanged: () => void;
  notify: (message: string, tone?: "ok" | "error") => void;
  goTo: (page: string) => void;
}) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [checks, setChecks] = useState<Check[] | null>(null);

  // The same checks `fath doctor` runs. Without this the dashboard reported a
  // clean overview while the CLI had been failing on 122 over-length folders.
  useEffect(() => {
    api
      .doctor()
      .then((result) => setChecks(result.checks))
      .catch(() => setChecks(null));
  }, [status?.meetings, status?.lastRun?.id]);

  const problems = (checks ?? []).filter((c) => c.status !== "ok");

  async function saveKey() {
    setBusy(true);
    try {
      await api.setKey(key.trim());
      setKey("");
      notify("API key saved to .env");
      onChanged();
    } catch (exception) {
      notify(String((exception as Error).message), "error");
    } finally {
      setBusy(false);
    }
  }

  async function verify() {
    setBusy(true);
    try {
      const result = await api.verifyKey();
      notify(`Key works: ${result.visibleMeetings} meetings on the first page`);
    } catch (exception) {
      notify(String((exception as Error).message), "error");
    } finally {
      setBusy(false);
    }
  }

  async function adopt() {
    setBusy(true);
    try {
      const result = await api.importFolders();
      notify(
        result.adopted
          ? `Adopted ${result.adopted} folder(s) with no API requests`
          : "Nothing new to adopt",
      );
      onChanged();
    } catch (exception) {
      notify(String((exception as Error).message), "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <img
        src={hero}
        alt=""
        className="h-28 w-full rounded-lg border border-surface0 object-cover object-[center_68%] sm:h-36 lg:col-span-2"
      />
      <Panel title="Overview" icon="dashboard" className="lg:col-span-2">
        <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
          <Stat label="Meetings" value={status?.meetings ?? "-"} />
          <Stat
            label="API key"
            value={status?.key.set ? `...${status.key.tail}` : "not set"}
            tone={status?.key.set ? "green" : "red"}
            hint={status?.key.set ? status.key.source : "paste one below"}
          />
          <Stat
            label="Last sync"
            value={relative(status?.lastRun?.started_at)}
            hint={
              status?.lastRun
                ? `${status.lastRun.written} written, ${status.lastRun.requests_made} requests`
                : undefined
            }
          />
          <Stat
            label="Search"
            value={status?.searchIndex === "fts5" ? "full text" : "basic"}
            tone={status?.searchIndex === "fts5" ? "green" : "yellow"}
          />
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="primary" icon="sync" onClick={() => goTo("sync")}>
            Sync meetings
          </Button>
          <Button icon="folder_open" onClick={adopt} disabled={busy}>
            Adopt folders on disk
          </Button>
          <Button variant="ghost" icon="search" onClick={() => goTo("search")}>
            Search transcripts
          </Button>
        </div>
      </Panel>

      {problems.length > 0 && (
        <Panel
          title={`Needs attention (${problems.length})`}
          icon="warning"
          className="lg:col-span-2 border-yellow/40"
        >
          <div className="space-y-3">
            {problems.map((check) => (
              <div key={check.check} className="flex gap-2.5">
                <Icon
                  name={check.status === "fail" ? "error" : "warning"}
                  className={`mt-0.5 text-[18px] ${
                    check.status === "fail" ? "text-red" : "text-yellow"
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <p className="text-sm">{check.message}</p>
                  {check.hint && (
                    <p className="mt-0.5 text-xs leading-relaxed text-overlay">{check.hint}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {checks !== null && problems.length === 0 && (
        <Panel className="lg:col-span-2 border-green/30">
          <p className="flex items-center gap-2 text-sm text-green">
            <Icon name="check_circle" className="text-[18px]" />
            Everything checks out: key, output folder, database and folder lengths.
          </p>
        </Panel>
      )}

      <Panel title="Fathom API key" icon="key">
        {status?.key.set ? (
          <p className="mb-3 text-sm text-subtext">
            A key ending <code className="font-mono text-text">...{status.key.tail}</code> is
            in use, read from{" "}
            <code className="font-mono text-text">{status.key.source}</code>. Replacing it
            below rewrites only that one line of the file.
          </p>
        ) : (
          <p className="mb-3 text-sm text-subtext">
            No key yet. Generate one at{" "}
            <a
              className="text-blue underline"
              href="https://fathom.video/customize#api-access-header"
              target="_blank"
              rel="noreferrer"
            >
              your Fathom settings
            </a>
            , then paste it here.
          </p>
        )}
        <div className="flex gap-2">
          <input
            type="password"
            value={key}
            placeholder="paste the key"
            onChange={(event) => setKey(event.target.value)}
            className="min-w-0 flex-1 rounded-md border border-surface0 bg-crust px-3 py-1.5 font-mono text-sm outline-none focus:border-blue"
          />
          <Button variant="primary" onClick={saveKey} disabled={!key.trim() || busy}>
            Save
          </Button>
          <Button onClick={verify} disabled={!status?.key.set || busy}>
            Test
          </Button>
        </div>
        <p className="mt-2 flex items-start gap-1.5 text-xs text-overlay">
          <Icon name="lock" className="text-[14px]" />
          Written to {status?.key.envFile ?? ".env"}, which is gitignored. The key is never
          sent back to this page.
        </p>
      </Panel>

      <Panel title="Where things live" icon="folder">
        <Field label="Meeting folders">
          <code className="block break-all font-mono text-xs text-subtext">
            {status?.outputRoot}
          </code>
          {status && !status.outputExists && (
            <span className="text-xs text-yellow">created on the first sync</span>
          )}
        </Field>
        <Field label="Settings and database" source={status?.settingsSource}>
          <code className="block break-all font-mono text-xs text-subtext">
            {status?.settingsRoot}
          </code>
        </Field>
        <p className="mt-2 text-xs leading-relaxed text-overlay">
          Folder names are lowercase and use only letters, numbers, dashes and
          underscores, so any path can be pasted into a shell or handed to an agent
          without quoting.
        </p>
      </Panel>
    </div>
  );
}
