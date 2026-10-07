// Every call to the Python server goes through here.
//
// The token is read once from the meta tag the server splices into index.html.
// It is sent on writes only, matching the server: reads over loopback are open
// so the page can load before it has anything to send.

export const TOKEN: string =
  document.querySelector<HTMLMetaElement>('meta[name="fath-token"]')?.content ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function unwrap(response: Response): Promise<unknown> {
  const text = await response.text();
  let body: unknown = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { error: text.slice(0, 300) };
  }
  if (!response.ok) {
    const message =
      typeof body === "object" && body !== null && "error" in body
        ? String((body as { error: unknown }).error)
        : `${response.status} ${response.statusText}`;
    throw new ApiError(message, response.status);
  }
  return body;
}

export async function get<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  return (await unwrap(response)) as T;
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-Fath-Token": TOKEN,
    },
    body: JSON.stringify(body ?? {}),
  });
  return (await unwrap(response)) as T;
}

// -- shapes the server returns ---------------------------------------------- //

export interface KeyState {
  set: boolean;
  source: string;
  tail: string;
  envFile: string;
}

export interface RunRow {
  id: number;
  started_at: string;
  finished_at: string | null;
  status: string;
  requests_made: number;
  meetings_seen: number;
  written: number;
  skipped: number;
  errors: number;
}

export interface Status {
  version: string;
  settingsRoot: string;
  settingsSource: string;
  outputRoot: string;
  outputExists: boolean;
  key: KeyState;
  meetings: number;
  searchIndex: string;
  lastRun: RunRow | null;
  activeRun: RunRow | null;
  running: boolean;
  rateLimit: { requestsPerWindow: number; windowSeconds: number; note: string };
}

export interface Meeting {
  recording_id: number;
  title: string;
  meeting_title: string;
  started_at: string;
  folder: string;
  attendees: string[];
  recorded_by: string;
  share_url: string;
  url: string;
  has_transcript: boolean;
  has_summary: boolean;
  action_item_count: number;
  transcript_chars: number;
}

export interface MeetingDetail extends Meeting {
  files: string[];
  folderPath: string;
  transcript: string;
  summary: string;
  actionItems: string;
}

export interface Setting {
  key: string;
  label: string;
  kind: string;
  group: string;
  note: string;
  options: string[];
  min: number | null;
  max: number | null;
  flags: string[];
  value: unknown;
  default: unknown;
  source: string;
}

export interface SearchHit {
  recording_id: number;
  speaker: string;
  timestamp: string;
  snippet: string;
  title: string;
  started_at: string;
  folder: string;
}

export interface Check {
  check: string;
  status: "ok" | "fail" | "note";
  message: string;
  hint: string;
}

export interface PreviewItem {
  recordingId: number;
  current: string;
  proposed: string;
  changed: boolean;
  error: string;
}

export interface RateSnapshot {
  limit: number | null;
  remaining: number | null;
  resetSec: number | null;
  paceSec: number;
  waitingSec: number;
  reason: string;
}

export interface ProgressEvent {
  id: number;
  ts: string;
  level: string;
  message: string;
  data: {
    kind?: string;
    folder?: string;
    index?: number;
    counts?: {
      seen: number;
      written: number;
      skipped: number;
      imported: number;
      errors: number;
      requests: number;
    };
    rate?: RateSnapshot | null;
  };
}

// -- endpoints --------------------------------------------------------------- //

export const api = {
  status: () => get<Status>("/api/status"),
  doctor: () => get<{ ok: boolean; checks: Check[] }>("/api/doctor"),
  meetings: (query: string, limit = 100, offset = 0) =>
    get<{ total: number; items: Meeting[] }>(
      `/api/meetings?q=${encodeURIComponent(query)}&limit=${limit}&offset=${offset}`,
    ),
  meeting: (id: number) => get<MeetingDetail>(`/api/meetings/${id}`),
  search: (query: string) =>
    get<{ query: string; hits: SearchHit[] }>(`/api/search?q=${encodeURIComponent(query)}`),
  settings: () => get<{ groups: string[]; settings: Setting[] }>("/api/settings"),
  saveSettings: (values: Record<string, unknown>) =>
    post<{ saved: string[]; settings: Setting[] }>("/api/settings", values),
  setKey: (key: string) => post<{ key: KeyState }>("/api/key", { key }),
  verifyKey: () => post<{ ok: boolean; visibleMeetings: number }>("/api/key/verify"),
  preview: (settings: Record<string, unknown>) =>
    post<{ items: PreviewItem[]; maxNameLength: number; outputRoot: string }>(
      "/api/naming/preview",
      { settings },
    ),
  startSync: (options: Record<string, unknown>) => post<{ runId: number }>("/api/sync", options),
  cancelSync: () => post<{ cancelling: boolean }>("/api/sync/cancel"),
  importFolders: (path?: string) =>
    post<{ adopted: number; path: string }>("/api/import", path ? { path } : {}),
};
