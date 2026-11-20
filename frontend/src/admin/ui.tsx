import type { ReactNode } from "react";

export type Message = { tone: "ok" | "error"; text: string } | null;

export function Notice({ message }: { message: Message }) {
  if (!message) return null;
  return (
    <p role={message.tone === "error" ? "alert" : "status"}
      className={`mb-3 rounded-lg px-3 py-2 text-sm ${message.tone === "error"
        ? "bg-rose-50 text-rose-700" : "bg-emerald-50 text-emerald-800"}`}>
      {message.text}
    </p>
  );
}

export function Loading({ error }: { error: string | null }) {
  return error
    ? <p role="alert" className="text-sm text-rose-700">{error}</p>
    : <p className="text-sm text-slate-400">Loading…</p>;
}

export function Table({ head, children, label }: { head: string[]; children: ReactNode; label: string }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table aria-label={label} className="w-full text-left text-sm">
        <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
          <tr>{head.map((h) => <th key={h} className="px-3 py-2 font-semibold">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
    </div>
  );
}

export const cell = "px-3 py-2 align-top";
export const smallButton = "rounded-md border border-slate-200 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50";

export function Pill({ children, tone = "slate" }: { children: ReactNode; tone?: "slate" | "green" | "amber" | "red" }) {
  const tones = { slate: "bg-slate-100 text-slate-700", green: "bg-emerald-100 text-emerald-800",
    amber: "bg-amber-100 text-amber-800", red: "bg-rose-100 text-rose-700" };
  return <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>{children}</span>;
}

export const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "never");
