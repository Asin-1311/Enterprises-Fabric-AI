import { useState } from "react";
import { listSources, registerSource, setSourcePaused, sourceStatus, type SourceStatus } from "./adminApi";
import { runAction, useLoad } from "./useLoad";
import { cell, Loading, Notice, Pill, smallButton, Table, when, type Message } from "./ui";

const TYPES = ["filesystem", "git", "s3", "webhook", "dms", "confluence", "jira", "sharepoint"];

/** Registered sources: status, pause/resume, freshness, and registering a new one. */
export function SourcesPanel() {
  const { data, error, reload } = useLoad(listSources);
  const [message, setMessage] = useState<Message>(null);
  const [status, setStatus] = useState<SourceStatus | null>(null);
  const [form, setForm] = useState({ id: "", type: "s3", config: '{\n  "bucket": "firm-documents",\n  "defaults": {"source_type": "firm_policy", "access_level": "staff"}\n}' });

  async function register() {
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(form.config);
    } catch {
      setMessage({ tone: "error", text: "The connector settings must be valid JSON." });
      return;
    }
    await runAction(() => registerSource({ id: form.id.trim(), type: form.type, connector_config: config }),
      `Source ${form.id.trim()} registered.`, setMessage, reload);
  }

  if (!data) return <Loading error={error} />;
  return (
    <div className="space-y-4">
      <Notice message={message} />
      <Table label="Sources" head={["Source", "Type", "Classification", "Status", "Last change", ""]}>
        {data.map((s) => (
          <tr key={s.id}>
            <td className={`${cell} font-medium`}>{s.id}</td>
            <td className={cell}>{s.type}</td>
            <td className={cell}>{s.classification}</td>
            <td className={cell}><Pill tone={s.status === "active" ? "green" : "amber"}>{s.status}</Pill></td>
            <td className={cell}>{when(s.last_event_at)}</td>
            <td className={`${cell} space-x-1 whitespace-nowrap text-right`}>
              <button type="button" className={smallButton}
                onClick={() => sourceStatus(s.id).then(setStatus).catch((e: Error) => setMessage({ tone: "error", text: e.message }))}>
                Status
              </button>
              <button type="button" className={smallButton}
                onClick={() => void runAction(() => setSourcePaused(s.id, s.status === "active"),
                  `${s.id} ${s.status === "active" ? "paused" : "resumed"}.`, setMessage, reload)}>
                {s.status === "active" ? "Pause" : "Resume"}
              </button>
            </td>
          </tr>
        ))}
      </Table>

      {status && (
        <section aria-label="Source status" className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
          <h3 className="mb-2 font-semibold">{status.id}</h3>
          <p>{status.documents} documents, {status.chunks} chunks · status {status.status}</p>
          <p>Last change: {when(status.last_event_at)} · freshness lag:{" "}
            {status.freshness_lag_seconds === null ? "no change seen yet" : `${Math.round(status.freshness_lag_seconds / 60)} min`}</p>
        </section>
      )}

      <form className="space-y-2 rounded-lg border border-slate-200 bg-white p-4"
        onSubmit={(e) => { e.preventDefault(); void register(); }}>
        <h3 className="text-sm font-semibold">Register a source</h3>
        <p className="text-xs text-slate-500">Secrets are never stored here: write <code>env:NAME</code> and put the value in .env.</p>
        <div className="flex gap-2">
          <input aria-label="Source id" value={form.id} onChange={(e) => setForm({ ...form, id: e.target.value })}
            placeholder="e.g. s3-policies" className="flex-1 rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
          <select aria-label="Source type" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}
            className="rounded-md border border-slate-200 px-2 py-1.5 text-sm">
            {TYPES.map((t) => <option key={t}>{t}</option>)}
          </select>
        </div>
        <textarea aria-label="Connector settings (JSON)" rows={5} value={form.config}
          onChange={(e) => setForm({ ...form, config: e.target.value })}
          className="w-full rounded-md border border-slate-200 px-2 py-1.5 font-mono text-xs" />
        <button type="submit" disabled={!form.id.trim()}
          className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300">Register</button>
      </form>
    </div>
  );
}
