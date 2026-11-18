import { ArrowUp, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { getHistory, getHistoryAnswer, getMe, logout, setSessionEndedHandler, streamQuery } from "./api";
import { AnswerCard, type Turn } from "./components/AnswerCard";
import { LoginScreen } from "./components/LoginScreen";
import { Sidebar } from "./components/Sidebar";
import { SourcePanel } from "./components/SourcePanel";
import { ThinkingDots } from "./components/ThinkingDots";
import { clearToken, currentToken, saveToken } from "./lib/session";
import type { Citation, HistoryItem, SessionInfo, User } from "./types";

const SUGGESTIONS = [
  "Within how many days must the final audit file be assembled?",
  "How do positive and negative external confirmations differ (SA 505)?",
  "What are the conditions for taking input tax credit under GST?",
  "When is a deferred tax asset recognised under AS 22?",
];

/** The reset token from a password-reset link (/?reset=<token>), if any. */
function resetTokenFromUrl(): string | null {
  return new URLSearchParams(window.location.search).get("reset");
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [restoring, setRestoring] = useState(() => currentToken() !== null);
  const [resetToken] = useState(resetTokenFromUrl);
  const [history, setHistory] = useState<HistoryItem[] | null>(null);
  const [activeQueryId, setActiveQueryId] = useState<number | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState<{ citations: Citation[]; activeId: string } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  function endSession() {
    abortRef.current?.abort();
    setUser(null);
    setTurns([]);
    setPanel(null);
    setHistory(null);
    setActiveQueryId(null);
  }

  function refreshHistory() {
    getHistory().then(setHistory).catch(() => setHistory((h) => h ?? []));
  }

  // A stored token (same tab, page reloaded) is checked with the API before it is trusted.
  useEffect(() => {
    setSessionEndedHandler(endSession);
    if (currentToken() === null) return;
    getMe()
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setRestoring(false));
  }, []);

  useEffect(() => {
    if (user) refreshHistory();
  }, [user]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [turns]);

  function signedIn(session: SessionInfo) {
    saveToken(session.token);
    setUser(session.user);
  }

  async function signOut() {
    endSession();
    await logout().catch(() => undefined);
  }

  function updateTurn(id: string, change: (t: Turn) => Turn) {
    setTurns((all) => all.map((t) => (t.id === id ? change(t) : t)));
  }

  function newConversation() {
    setTurns([]);
    setActiveQueryId(null);
    setPanel(null);
  }

  /** Show a past question and its stored answer (the dots show while it loads). */
  async function openHistory(item: HistoryItem) {
    if (busy) return;
    const id = `history-${item.query_id}`;
    setActiveQueryId(item.query_id);
    setPanel(null);
    setTurns([{ id, question: item.question, steps: [], text: "", final: null, error: null }]);
    try {
      const final = await getHistoryAnswer(item.query_id);
      updateTurn(id, (t) => ({ ...t, final }));
    } catch (err) {
      updateTurn(id, (t) => ({ ...t, error: (err as Error).message }));
    }
  }

  async function ask(question: string) {
    const text = question.trim();
    if (!text || busy) return;
    const id = crypto.randomUUID();
    setTurns((all) => [...all, { id, question: text, steps: [], text: "", final: null, error: null }]);
    setInput("");
    setBusy(true);
    setPanel(null);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamQuery(text, (event) => {
        if (event.event === "progress") updateTurn(id, (t) => ({ ...t, steps: [...t.steps, event.data.step] }));
        else if (event.event === "token") updateTurn(id, (t) => ({ ...t, text: t.text + event.data.text }));
        else if (event.event === "final") {
          updateTurn(id, (t) => ({ ...t, final: event.data }));
          setActiveQueryId(event.data.query_id);
          refreshHistory(); // the new question now heads the History list
        }
        else updateTurn(id, (t) => ({ ...t, error: event.data.message }));
      }, controller.signal);
    } catch (err) {
      const message = controller.signal.aborted ? "Stopped." : (err as Error).message;
      updateTurn(id, (t) => (t.final ? t : { ...t, error: message }));
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  if (restoring) {
    return (
      <div className="grid h-screen place-items-center bg-slate-50">
        <ThinkingDots label="Restoring your session" />
      </div>
    );
  }

  if (!user) return <LoginScreen onSignedIn={signedIn} resetToken={resetToken} />;

  return (
    <div className="flex h-screen bg-slate-50 text-slate-900">
      <Sidebar
        user={user}
        history={history}
        activeQueryId={activeQueryId}
        historyDisabled={busy}
        onOpenHistory={(item) => void openHistory(item)}
        onNewConversation={newConversation}
        onSignOut={() => void signOut()}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl space-y-8 px-6 py-8">
            {!turns.length && (
              <div className="pt-16 text-center">
                <h1 className="text-2xl font-semibold text-slate-900">How can I help you today?</h1>
                <p className="mt-2 text-sm text-slate-500">
                  Answers come only from documents you are allowed to see, with a citation for every
                  statement and a verified confidence score.
                </p>
                <div className="mt-8 grid gap-3 sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => void ask(s)}
                      disabled={busy}
                      className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-left text-sm text-slate-700 shadow-sm hover:border-indigo-300 hover:bg-indigo-50/40"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {turns.map((t) => (
              <AnswerCard key={t.id} turn={t} onOpenSource={(activeId, citations) => setPanel({ activeId, citations })} />
            ))}
            <div ref={bottomRef} />
          </div>
        </div>

        <form
          className="border-t border-slate-200 bg-white px-6 py-4"
          onSubmit={(e) => { e.preventDefault(); void ask(input); }}
        >
          <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-slate-300 bg-white px-3 py-2 shadow-sm focus-within:border-indigo-400 focus-within:ring-2 focus-within:ring-indigo-100">
            <textarea
              aria-label="Your question"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void ask(input); }
              }}
              rows={1}
              placeholder="Ask about SAs, Ind AS / AS, tax, firm policies, checklists or your engagements"
              className="max-h-40 flex-1 resize-none bg-transparent py-1.5 text-sm focus:outline-none"
            />
            {busy ? (
              <button type="button" aria-label="Stop" onClick={() => abortRef.current?.abort()}
                className="grid h-9 w-9 place-items-center rounded-xl bg-slate-800 text-white">
                <Square size={14} aria-hidden />
              </button>
            ) : (
              <button type="submit" aria-label="Send" disabled={!input.trim()}
                className="grid h-9 w-9 place-items-center rounded-xl bg-indigo-600 text-white disabled:bg-slate-300">
                <ArrowUp size={18} aria-hidden />
              </button>
            )}
          </div>
          <p className="mx-auto mt-2 max-w-3xl text-center text-[11px] text-slate-400">
            Decision support only: professional judgement and sign-off remain with the engagement partner.
          </p>
        </form>
      </main>

      {panel && (
        <SourcePanel
          citations={panel.citations}
          activeId={panel.activeId}
          onSelect={(id) => setPanel({ ...panel, activeId: id })}
          onClose={() => setPanel(null)}
        />
      )}
    </div>
  );
}
