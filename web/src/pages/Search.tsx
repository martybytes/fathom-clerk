import { useState } from "react";
import { api, type SearchHit } from "../lib/api";
import { Button, Empty, Panel, Spinner } from "../components/ui";

/** SQLite marks matches with [brackets]; turn those into emphasis.
 *
 *  Escaped first. The snippet is meeting content, not ours, and a transcript
 *  containing a stray angle bracket must not become markup.
 */
function highlight(snippet: string): string {
  const escaped = snippet
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
  return escaped.replace(
    /\[([^\]]+)\]/g,
    '<mark class="bg-yellow/25 text-yellow rounded px-0.5">$1</mark>',
  );
}

export default function Search() {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);

  function run() {
    if (!query.trim()) return;
    setBusy(true);
    api
      .search(query)
      .then((result) => setHits(result.hits))
      .finally(() => setBusy(false));
  }

  return (
    <Panel title="Search every transcript" icon="manage_search">
      <div className="flex gap-2">
        <input
          value={query}
          autoFocus
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && run()}
          placeholder="what did anyone say about..."
          className="min-w-0 flex-1 rounded-md border border-surface0 bg-crust px-3 py-2 text-sm outline-none focus:border-blue"
        />
        <Button variant="primary" icon="search" onClick={run} disabled={busy}>
          Search
        </Button>
      </div>
      <p className="mt-2 text-xs text-overlay">
        Searches what has already been synced, so this costs no API requests.
      </p>

      <div className="mt-4">
        {busy && <Spinner />}
        {hits === null && !busy && (
          <Empty icon="search" title="Search across every meeting you have synced" />
        )}
        {hits?.length === 0 && <Empty icon="search_off" title="No matches" />}
        <div className="space-y-2">
          {hits?.map((hit, index) => (
            <div
              key={`${hit.recording_id}-${index}`}
              className="rounded-md border border-surface0 bg-crust p-3"
            >
              <div className="flex items-baseline justify-between gap-2 text-xs text-overlay">
                <span className="truncate font-medium text-subtext">{hit.title}</span>
                <span className="shrink-0 font-mono">
                  {hit.started_at?.slice(0, 10)} &middot; {hit.timestamp}
                </span>
              </div>
              <p className="mt-1 text-sm leading-relaxed">
                <span className="text-mauve">{hit.speaker}:</span>{" "}
                <span dangerouslySetInnerHTML={{ __html: highlight(hit.snippet) }} />
              </p>
              <code className="mt-1 block truncate font-mono text-[11px] text-overlay">
                {hit.folder}
              </code>
            </div>
          ))}
        </div>
      </div>
    </Panel>
  );
}
