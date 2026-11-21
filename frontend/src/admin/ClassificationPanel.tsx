import { useState } from "react";
import {
  createClassificationPolicy, deactivateClassificationPolicy, listClassificationPolicies,
  type ClassificationPolicy,
} from "./adminApi";
import { useLoad } from "./useLoad";
import { cell, Loading, Notice, Pill, smallButton, Table, when, type Message } from "./ui";

const SOURCE_TYPES = ["standards", "firm_policy", "circulars", "checklists", "templates", "engagement", "calendar", "faq"];

/** Classification rules: "documents of this source type / path are at least X". They only
 * ever make documents stricter, and apply to the matching documents at once. */
export function ClassificationPanel() {
  const { data, error, reload } = useLoad(listClassificationPolicies);
  const [message, setMessage] = useState<Message>(null);
  const [form, setForm] = useState<Pick<ClassificationPolicy, "match" | "pattern" | "classification">>(
    { match: "source_type", pattern: "engagement", classification: "confidential" });

  async function apply(change: () => Promise<{ reclassified: string[] }>, what: string) {
    try {
      const { reclassified } = await change();
      setMessage({ tone: "ok", text: `${what} ${reclassified.length} document(s) reclassified.` });
      reload();
    } catch (err) {
      setMessage({ tone: "error", text: (err as Error).message });
    }
  }

  if (!data) return <Loading error={error} />;
  return (
    <div className="space-y-4">
      <Notice message={message} />
      <Table label="Classification rules" head={["#", "Documents", "At least", "State", "Created", ""]}>
        {data.map((p) => (
          <tr key={p.id}>
            <td className={cell}>{p.id}</td>
            <td className={cell}>{p.match === "source_type" ? `source type ${p.pattern}` : `path ${p.pattern}`}</td>
            <td className={cell}><Pill tone={p.classification === "restricted" ? "red" : "amber"}>{p.classification}</Pill></td>
            <td className={cell}>{p.active ? <Pill tone="green">active</Pill> : <Pill>off since {when(p.deactivated_at)}</Pill>}</td>
            <td className={cell}>{p.created_by}, {when(p.created_at)}</td>
            <td className={`${cell} text-right`}>
              {p.active && (
                <button type="button" className={smallButton} aria-label={`Switch off rule ${p.id}`}
                  onClick={() => void apply(() => deactivateClassificationPolicy(p.id), `Rule ${p.id} switched off:`)}>
                  Switch off
                </button>
              )}
            </td>
          </tr>
        ))}
        {data.length === 0 && <tr><td className={cell} colSpan={6}>No rules yet.</td></tr>}
      </Table>

      <form className="space-y-2 rounded-lg border border-slate-200 bg-white p-4"
        onSubmit={(e) => { e.preventDefault(); void apply(() => createClassificationPolicy(form), "Rule added:"); }}>
        <h3 className="text-sm font-semibold">Add a rule</h3>
        <p className="text-xs text-slate-500">
          Restricted documents are read only by the firm's private model. A rule never lowers a document's own classification.
        </p>
        <div className="flex flex-wrap gap-2">
          <select aria-label="Match by" value={form.match}
            onChange={(e) => setForm({ ...form, match: e.target.value as "source_type" | "path",
              pattern: e.target.value === "path" ? "*/engagement/*" : "engagement" })}
            className="rounded-md border border-slate-200 px-2 py-1.5 text-sm">
            <option value="source_type">Source type</option>
            <option value="path">Path (glob)</option>
          </select>
          {form.match === "source_type" ? (
            <select aria-label="Source type" value={form.pattern} onChange={(e) => setForm({ ...form, pattern: e.target.value })}
              className="rounded-md border border-slate-200 px-2 py-1.5 text-sm">
              {SOURCE_TYPES.map((t) => <option key={t}>{t}</option>)}
            </select>
          ) : (
            <input aria-label="Path pattern" value={form.pattern} onChange={(e) => setForm({ ...form, pattern: e.target.value })}
              className="flex-1 rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
          )}
          <select aria-label="Minimum classification" value={form.classification}
            onChange={(e) => setForm({ ...form, classification: e.target.value as "confidential" | "restricted" })}
            className="rounded-md border border-slate-200 px-2 py-1.5 text-sm">
            <option value="confidential">confidential</option>
            <option value="restricted">restricted</option>
          </select>
          <button type="submit" className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white">Add rule</button>
        </div>
      </form>
    </div>
  );
}
