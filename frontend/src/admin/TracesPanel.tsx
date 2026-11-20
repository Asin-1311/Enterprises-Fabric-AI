import { useState } from "react";
import { getTrace, recentQueries, type TraceRow } from "./adminApi";
import { useLoad } from "./useLoad";
import { cell, Loading, Pill, smallButton, Table, when } from "./ui";

/** Recent questions and the agent-by-agent trace of one. Administrators who are not
 * partners see how each step ran, never question text or document content. */
export function TracesPanel() {
  const { data, error } = useLoad(recentQueries);
  const [open, setOpen] = useState<{ id: number; rows: TraceRow[] } | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);

  function show(id: number) {
    setTraceError(null);
    getTrace(id).then((rows) => setOpen({ id, rows })).catch((e: Error) => setTraceError(e.message));
  }

  if (!data) return <Loading error={error} />;
  return (
    <div className="space-y-4">
      <Table label="Recent questions" head={["#", "Asked", "User", "Question", "Intent", "Outcome", ""]}>
        {data.map((q) => (
          <tr key={q.query_id}>
            <td className={cell}>{q.query_id}</td>
            <td className={cell}>{when(q.asked_at)}</td>
            <td className={cell}>{q.user_id}</td>
            <td className={`${cell} max-w-xs`}>
              {q.question_hidden ? <span className="text-slate-400">hidden (partners only)</span> : q.question}
            </td>
            <td className={cell}>{q.intent ?? "—"}</td>
            <td className={cell}><Pill tone={q.status === "answered" ? "green" : "slate"}>{q.status ?? "—"}</Pill></td>
            <td className={`${cell} text-right`}>
              <button type="button" className={smallButton} onClick={() => show(q.query_id)}>Trace</button>
            </td>
          </tr>
        ))}
      </Table>
      {traceError && <p role="alert" className="text-sm text-rose-700">{traceError}</p>}
      {open && (
        <section aria-label={`Trace of question ${open.id}`}>
          <h3 className="mb-2 text-sm font-semibold">Trace of question {open.id}</h3>
          {open.rows.some((r) => r.content_hidden) && (
            <p className="mb-2 text-xs text-slate-500">
              Inputs and outputs are hidden: they contain document text, and the admin flag grants no document access.
            </p>
          )}
          <Table label="Trace steps" head={["Step", "Model", "Tokens in/out", "Latency", "Prompt v.", "Policy v."]}>
            {open.rows.map((r, i) => (
              <tr key={i}>
                <td className={`${cell} font-medium`}>{r.agent}</td>
                <td className={cell}>{r.model ?? "—"}{r.effort ? ` (${r.effort})` : ""}</td>
                <td className={cell}>{r.tokens_in ?? "—"} / {r.tokens_out ?? "—"}</td>
                <td className={cell}>{r.latency_ms === null ? "—" : `${Math.round(r.latency_ms)} ms`}</td>
                <td className={cell}>{r.prompt_version_id ?? "—"}</td>
                <td className={cell}>{r.policy_version_id ?? "—"}</td>
              </tr>
            ))}
          </Table>
        </section>
      )}
    </div>
  );
}
