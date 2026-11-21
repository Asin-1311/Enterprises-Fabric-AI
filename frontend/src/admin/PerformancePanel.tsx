import { agentPerformance } from "./adminApi";
import { useLoad } from "./useLoad";
import { cell, Loading, Table } from "./ui";

const pct = (x: number | null) => (x === null ? "—" : `${(x * 100).toFixed(1)}%`);
const ms = (x: number | null) => (x === null ? "—" : `${Math.round(x)} ms`);

/** Agent performance over the last 7 days, computed by the API from the stored traces. */
export function PerformancePanel() {
  const { data, error, reload } = useLoad(agentPerformance);
  if (!data) return <Loading error={error} />;
  const stats: [string, string][] = [
    ["Answers", String(data.answers)],
    ["Verification failure rate", pct(data.verification_failure_rate)],
    ["Reduced checking (degraded)", pct(data.degraded_rate)],
    ["Answered from cache", pct(data.cache_hit_rate)],
    ["Cost per answer (estimate)", data.estimated_cost_per_answer_usd === null ? "—"
      : `$${data.estimated_cost_per_answer_usd.toFixed(4)}`],
  ];
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between text-xs text-slate-500">
        <span>{new Date(data.from).toLocaleDateString()} – {new Date(data.to).toLocaleDateString()}</span>
        <button type="button" onClick={reload} className="underline">Refresh</button>
      </div>
      <dl className="grid gap-3 sm:grid-cols-5">
        {stats.map(([label, value]) => (
          <div key={label} className="rounded-lg border border-slate-200 bg-white p-3">
            <dt className="text-xs text-slate-500">{label}</dt>
            <dd className="mt-1 text-lg font-semibold text-slate-900">{value}</dd>
          </div>
        ))}
      </dl>
      <Table label="Agents" head={["Agent", "Steps", "Latency p50", "Latency p95", "Tokens in", "Tokens out"]}>
        {data.agents.map((a) => (
          <tr key={a.agent}>
            <td className={`${cell} font-medium`}>{a.agent}</td>
            <td className={cell}>{a.steps}</td>
            <td className={cell}>{ms(a.latency_p50_ms)}</td>
            <td className={cell}>{ms(a.latency_p95_ms)}</td>
            <td className={cell}>{a.tokens_in}</td>
            <td className={cell}>{a.tokens_out}</td>
          </tr>
        ))}
      </Table>
      <p className="text-xs text-slate-400">{data.cost_basis}.</p>
    </div>
  );
}
