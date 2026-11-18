import { BookOpen, History, LogOut, MessageSquarePlus, ShieldCheck } from "lucide-react";
import { groupHistory } from "../lib/history";
import type { HistoryItem, User } from "../types";

const ROLE_STYLE: Record<User["role"], string> = {
  staff: "bg-sky-100 text-sky-800",
  manager: "bg-amber-100 text-amber-800",
  partner: "bg-violet-100 text-violet-800",
};

function initials(name: string): string {
  return name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
}

interface Props {
  user: User;
  history: HistoryItem[] | null; // null while loading
  activeQueryId: number | null;
  historyDisabled: boolean; // while an answer is being prepared
  onOpenHistory: (item: HistoryItem) => void;
  onNewConversation: () => void;
  onSignOut: () => void;
}

export function Sidebar({
  user, history, activeQueryId, historyDisabled, onOpenHistory, onNewConversation, onSignOut,
}: Props) {
  return (
    <aside className="flex h-full w-80 shrink-0 flex-col border-r border-slate-200 bg-white">
      <div className="flex items-center gap-3 border-b border-slate-200 px-5 py-4">
        <div className="grid h-10 w-10 place-items-center rounded-xl bg-indigo-600 text-white shadow-sm">
          <BookOpen size={20} aria-hidden />
        </div>
        <div>
          <div className="text-base font-semibold text-slate-900">Enterprise Fabric AI</div>
          <div className="text-xs text-slate-500">Cited answers for audit teams</div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        <button
          type="button"
          onClick={onNewConversation}
          className="mb-5 flex w-full items-center justify-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          <MessageSquarePlus size={16} aria-hidden /> New conversation
        </button>

        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Signed in as</h2>
        <div className="flex items-center gap-3 rounded-lg px-2 py-2">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-indigo-100 text-xs font-semibold text-indigo-800">
            {initials(user.name)}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium text-slate-900">{user.name}</span>
            <span className="block truncate text-xs text-slate-500">{user.email ?? user.user_id}</span>
          </span>
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${ROLE_STYLE[user.role]}`}>
            {user.role}
          </span>
        </div>

        <div className="mt-2 rounded-lg bg-slate-50 p-3">
          <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-slate-600">
            <ShieldCheck size={14} aria-hidden /> Your access
          </div>
          <p className="text-xs text-slate-600">
            Role: <span className="font-medium capitalize">{user.role}</span>
          </p>
          <p className="text-xs text-slate-600">
            Engagements:{" "}
            <span className="font-medium">{user.engagements.length ? user.engagements.join(", ") : "none"}</span>
          </p>
        </div>

        <button
          type="button"
          onClick={onSignOut}
          className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 hover:text-slate-900"
        >
          <LogOut size={15} aria-hidden /> Sign out
        </button>

        <nav aria-label="History" className="mt-6">
          <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
            <History size={13} aria-hidden /> History
          </h2>
          {history === null ? (
            <p className="px-2 text-xs text-slate-400">Loading…</p>
          ) : history.length === 0 ? (
            <p className="px-2 text-xs text-slate-400">Your questions will appear here.</p>
          ) : (
            groupHistory(history).map((group) => (
              <div key={group.label} className="mb-3">
                <h3 className="px-2 pb-1 text-[11px] font-medium text-slate-400">{group.label}</h3>
                <ul className="space-y-0.5">
                  {group.items.map((item) => {
                    const active = item.query_id === activeQueryId;
                    return (
                      <li key={item.query_id}>
                        <button
                          type="button"
                          title={item.question}
                          aria-current={active ? "true" : undefined}
                          disabled={historyDisabled}
                          onClick={() => onOpenHistory(item)}
                          className={`block w-full truncate rounded-md px-2 py-1.5 text-left text-sm transition disabled:cursor-not-allowed disabled:opacity-60 ${
                            active ? "bg-indigo-50 font-medium text-indigo-800" : "text-slate-700 hover:bg-slate-50"
                          }`}
                        >
                          {item.question}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))
          )}
        </nav>
      </div>

      <p className="border-t border-slate-200 px-5 py-3 text-[11px] leading-snug text-slate-400">
        SYNTHETIC demonstration for an internship project: fictional firm, clients and people.
        Answers support, and do not replace, professional judgement.
      </p>
    </aside>
  );
}
