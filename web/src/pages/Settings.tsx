import { useEffect, useMemo, useState } from "react";
import { api, type PreviewItem, type Setting } from "../lib/api";
import { Button, Field, Icon, Panel, Spinner } from "../components/ui";

/** Everything here renders from the server's schema, so a setting added to
 *  fath/schema.py appears in this screen, the terminal one and the CLI at once. */
export default function Settings({
  notify,
  onChanged,
}: {
  notify: (message: string, tone?: "ok" | "error") => void;
  onChanged: () => void;
}) {
  const [settings, setSettings] = useState<Setting[]>([]);
  const [groups, setGroups] = useState<string[]>([]);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [preview, setPreview] = useState<PreviewItem[]>([]);
  const [maxName, setMaxName] = useState(0);
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = () =>
    api.settings().then((result) => {
      setSettings(result.settings);
      setGroups(result.groups);
      setDraft({});
    });

  useEffect(() => {
    load();
  }, []);

  const namingDraft = useMemo(
    () => Object.fromEntries(Object.entries(draft).filter(([k]) => k.startsWith("naming."))),
    [draft],
  );

  useEffect(() => {
    // Previewing against unsaved values is the whole point: the naming rules are
    // hard to reason about in the abstract and trivial to judge from a list.
    const timer = setTimeout(() => {
      api
        .preview(namingDraft)
        .then((result) => {
          setPreview(result.items);
          setMaxName(result.maxNameLength);
        })
        .catch(() => undefined);
    }, 250);
    return () => clearTimeout(timer);
  }, [namingDraft]);

  const dirty = Object.keys(draft).length > 0;

  async function save() {
    setBusy(true);
    try {
      await api.saveSettings(draft);
      notify(`Saved ${Object.keys(draft).length} setting(s)`);
      await load();
      onChanged();
    } catch (exception) {
      notify(String((exception as Error).message), "error");
    } finally {
      setBusy(false);
    }
  }

  function value(setting: Setting): unknown {
    return setting.key in draft ? draft[setting.key] : setting.value;
  }

  function control(setting: Setting) {
    const current = value(setting);
    const set = (next: unknown) => setDraft({ ...draft, [setting.key]: next });

    if (setting.kind === "bool") {
      return (
        <label className="inline-flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            checked={Boolean(current)}
            onChange={(event) => set(event.target.checked)}
            className="accent-blue"
          />
          <span className="text-xs text-overlay">{current ? "on" : "off"}</span>
        </label>
      );
    }
    if (setting.kind === "choice") {
      return (
        <select
          value={String(current ?? "")}
          onChange={(event) => set(event.target.value)}
          className="w-full rounded-md border border-surface0 bg-crust px-2 py-1.5 text-sm outline-none focus:border-blue"
        >
          {setting.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      );
    }
    if (setting.kind === "int" || setting.kind === "float") {
      return (
        <input
          type="number"
          value={String(current ?? "")}
          min={setting.min ?? undefined}
          max={setting.max ?? undefined}
          step={setting.kind === "float" ? "0.1" : "1"}
          onChange={(event) =>
            set(setting.kind === "int" ? Number(event.target.value) : Number(event.target.value))
          }
          className="w-full rounded-md border border-surface0 bg-crust px-2 py-1.5 text-sm outline-none focus:border-blue"
        />
      );
    }
    if (setting.kind === "list" || setting.kind === "map") {
      return (
        <textarea
          rows={3}
          value={typeof current === "string" ? current : JSON.stringify(current, null, 0)}
          onChange={(event) => {
            try {
              set(JSON.parse(event.target.value));
            } catch {
              set(event.target.value); // let the server reject it with a reason
            }
          }}
          className="w-full rounded-md border border-surface0 bg-crust px-2 py-1.5 font-mono text-xs outline-none focus:border-blue"
        />
      );
    }
    return (
      <input
        value={String(current ?? "")}
        onChange={(event) => set(event.target.value)}
        className="w-full rounded-md border border-surface0 bg-crust px-2 py-1.5 font-mono text-sm outline-none focus:border-blue"
      />
    );
  }

  const visible = settings.filter((s) => advanced || !s.flags.includes("advanced"));

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_24rem]">
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <label className="flex cursor-pointer items-center gap-2 text-xs text-overlay">
            <input
              type="checkbox"
              checked={advanced}
              onChange={(event) => setAdvanced(event.target.checked)}
              className="accent-blue"
            />
            show advanced settings
          </label>
          <div className="flex items-center gap-2">
            {dirty && <span className="text-xs text-yellow">unsaved changes</span>}
            {busy && <Spinner />}
            <Button variant="ghost" onClick={() => setDraft({})} disabled={!dirty}>
              Discard
            </Button>
            <Button variant="primary" icon="save" onClick={save} disabled={!dirty || busy}>
              Save
            </Button>
          </div>
        </div>

        {groups.map((group) => {
          const inGroup = visible.filter((s) => s.group === group);
          if (inGroup.length === 0) return null;
          return (
            <Panel key={group} title={group} icon={groupIcon(group)}>
              {inGroup.map((setting) => (
                <Field
                  key={setting.key}
                  label={setting.label}
                  note={setting.note}
                  source={setting.key in draft ? undefined : setting.source}
                >
                  {control(setting)}
                </Field>
              ))}
            </Panel>
          );
        })}
      </div>

      <div className="lg:sticky lg:top-4 lg:self-start">
        <Panel title="Folder name preview" icon="preview">
          <p className="mb-3 text-xs leading-relaxed text-overlay">
            Names produced by the rules on the left, against your real meetings. Nothing
            is saved until you press Save, and changing a rule does not rename anything
            already on disk.
          </p>
          {maxName > 0 && (
            <p className="mb-3 flex items-start gap-1.5 text-xs text-subtext">
              <Icon name="straighten" className="text-[14px]" />
              Names are capped at {maxName} characters, worked out from your output folder
              so the full path stays inside the Windows limit.
            </p>
          )}
          <div className="max-h-[30rem] space-y-1.5 overflow-y-auto">
            {preview.map((item) => (
              <div key={item.recordingId} className="font-mono text-[11px] leading-snug">
                {item.error ? (
                  <span className="text-red">{item.error}</span>
                ) : item.changed ? (
                  <>
                    <div className="text-overlay line-through">{item.current}</div>
                    <div className="text-green">{item.proposed}</div>
                  </>
                ) : (
                  <div className="text-subtext">{item.proposed}</div>
                )}
              </div>
            ))}
            {preview.length === 0 && (
              <p className="text-xs text-overlay">
                Nothing synced yet, so there is nothing to preview against.
              </p>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function groupIcon(group: string): string {
  return (
    {
      Locations: "folder",
      Downloads: "download",
      Naming: "label",
      Sync: "sync",
      "Rate limiting": "speed",
      Web: "language",
    }[group] ?? "settings"
  );
}
