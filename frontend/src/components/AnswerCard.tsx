import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { AlertTriangle, Clock, Copy, FileText, Info, MoreHorizontal, ShieldAlert, Sparkles } from "lucide-react";
import Markdown from "react-markdown";
import { citationIdFromHref, numberCitations, pageLabel, stripCitations } from "../lib/citations";
import type { SignalKind } from "../lib/signals";
import type { AnswerStatus, Citation, ClaimVerdict, FinalPayload } from "../types";
import { CheckThisPoint, ReducedCheckingBadge, SupersededBadge } from "./Badges";
import { ConfidenceGauge } from "./ConfidenceGauge";
import { DraftView } from "./DraftView";
import { FeedbackBox } from "./FeedbackBox";
import { ProgressTimeline } from "./ProgressTimeline";
import { ThinkingDots } from "./ThinkingDots";

export interface Turn {
  id: string;
  question: string;
  steps: string[];
  draft: string; // the draft as it is written, before verification (M16b)
  verdicts: ClaimVerdict[]; // one per checked claim of the draft
  text: string; // verified answer text streamed so far
  final: FinalPayload | null;
  error: string | null;
}

const STATUS_NOTE: Partial<Record<AnswerStatus, { tone: string; text: string }>> = {
  denied: { tone: "bg-slate-100 text-slate-700", text: "Not available for your role or engagement assignments." },
  not_found: { tone: "bg-slate-100 text-slate-700", text: "Nothing suitable was found in the material you can access." },
  abstained: {
    tone: "bg-amber-50 text-amber-800",
    text: "Not enough could be verified. Rephrase the question, or ask your engagement manager.",
  },
  restricted_unavailable: {
    tone: "bg-amber-50 text-amber-800",
    text: "Restricted material is read only by the firm's private model, which is not available now.",
  },
};

interface Props {
  turn: Turn;
  onOpenSource: (chunkId: string, citations: Citation[]) => void;
  onSignal?: (answerId: number, kind: SignalKind) => void;
}

const FLAG_TEXT = "(check this point)"; // the verifier's marker (app/agents/verifier.py FLAG_MARK)

export function AnswerCard({ turn, onOpenSource, onSignal }: Props) {
  const { final } = turn;
  const { markdown, order } = numberCitations(final?.text ?? turn.text);
  const byId = new Map((final?.citations ?? []).map((c) => [c.chunk_id, c]));
  const ordered = [
    ...order.map((id) => byId.get(id)).filter((c): c is Citation => Boolean(c)),
    ...(final?.citations ?? []).filter((c) => !order.includes(c.chunk_id)),
  ];
  const note = final ? STATUS_NOTE[final.status] : undefined;
  const signal = (kind: SignalKind) => { if (final) onSignal?.(final.answer_id, kind); };
  const openSource = (id: string) => { signal("citation_click"); onOpenSource(id, ordered); };
  const showDraft = !final && !turn.error && !turn.text && turn.draft !== "";

  async function copyAnswer() {
    if (!final) return;
    try {
      await navigator.clipboard?.writeText(stripCitations(final.text));
    } catch {
      // no clipboard permission: the user can still select and copy the text
    }
    signal("copy");
  }

  return (
    <div className="space-y-4">
      {/* The question */}
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-md bg-indigo-600 px-4 py-2.5 text-sm text-white shadow-sm">
          {turn.question}
        </div>
      </div>

      {/* The answer */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-indigo-700">
          <Sparkles size={14} aria-hidden /> Enterprise Fabric AI
          {final && final.text && (
            <DropdownMenu.Root>
              <DropdownMenu.Trigger aria-label="Answer actions"
                className="ml-auto rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
                <MoreHorizontal size={16} aria-hidden />
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content align="end" sideOffset={4}
                  className="z-50 min-w-40 rounded-lg border border-slate-200 bg-white p-1 text-sm normal-case tracking-normal text-slate-700 shadow-lg">
                  <DropdownMenu.Item onSelect={() => void copyAnswer()}
                    className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 outline-none data-[highlighted]:bg-indigo-50">
                    <Copy size={14} aria-hidden /> Copy answer
                  </DropdownMenu.Item>
                  {ordered.length > 0 && (
                    <DropdownMenu.Item onSelect={() => openSource(ordered[0].chunk_id)}
                      className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 outline-none data-[highlighted]:bg-indigo-50">
                      <FileText size={14} aria-hidden /> Show sources
                    </DropdownMenu.Item>
                  )}
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          )}
        </div>

        {!final && !turn.error && (
          <div className="space-y-3">
            {!showDraft && <ThinkingDots />}
            <ProgressTimeline steps={turn.steps} done={false} />
          </div>
        )}
        {showDraft && <DraftView draft={turn.draft} verdicts={turn.verdicts} />}
        {turn.error && (
          <p className="flex items-center gap-2 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">
            <AlertTriangle size={16} aria-hidden /> {turn.error}
          </p>
        )}
        {note && <p className={`mb-3 rounded-lg px-3 py-2 text-sm ${note.tone}`}>{note.text}</p>}

        {markdown && !showDraft && (
          <div className="answer-markdown mt-3 text-[15px] leading-relaxed text-slate-800"
            onCopy={() => signal("copy")}>
            <Markdown
              components={{
                em: ({ children }) =>
                  String(children) === FLAG_TEXT ? <CheckThisPoint /> : <em>{children}</em>,
                a: ({ href, children }) => {
                  const id = citationIdFromHref(href);
                  if (!id) return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
                  return (
                    <button
                      type="button"
                      title={byId.get(id)?.title ?? id}
                      onClick={() => openSource(id)}
                      disabled={!byId.has(id)}
                      className="mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-md bg-indigo-50 px-1.5 align-text-top text-[11px] font-semibold text-indigo-700 hover:bg-indigo-100 disabled:cursor-default"
                    >
                      {children}
                    </button>
                  );
                },
              }}
            >
              {markdown}
            </Markdown>
          </div>
        )}

        {final && (
          <>
            <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-slate-100 pt-4">
              {final.confidence !== null && (
                <ConfidenceGauge value={final.confidence} supported={final.claims_supported} factual={final.claims_factual} />
              )}
              {final.seconds_to_answer !== null && (
                <span className="flex items-center gap-1 text-xs text-slate-500">
                  <Clock size={13} aria-hidden /> {final.seconds_to_answer.toFixed(1)} s
                </span>
              )}
              {final.degraded && <ReducedCheckingBadge />}
              {final.superseded_involved && <SupersededBadge />}
              {(final.flagged_claims?.length ?? 0) > 0 && (
                <span className="inline-flex items-center gap-1 text-xs text-amber-800">
                  <AlertTriangle size={13} aria-hidden /> {final.flagged_claims!.length} point(s) to check against the source
                </span>
              )}
              {final.removed_claims > 0 && (
                <span className="inline-flex items-center gap-1 text-xs text-slate-500">
                  <Info size={13} aria-hidden /> {final.removed_claims} unverified statement(s) removed
                </span>
              )}
              {final.injection_flagged_chunks > 0 && (
                <span className="inline-flex items-center gap-1 text-xs text-slate-500">
                  <ShieldAlert size={13} aria-hidden /> Instruction-like text in {final.injection_flagged_chunks} source(s) ignored
                </span>
              )}
            </div>

            {ordered.length > 0 && (
              <div className="mt-4">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Sources</h3>
                <ul className="grid gap-2 sm:grid-cols-2">
                  {ordered.map((c, i) => (
                    <li key={c.chunk_id}>
                      <button
                        type="button"
                        onClick={() => openSource(c.chunk_id)}
                        className="flex h-full w-full items-start gap-2 rounded-lg border border-slate-200 px-3 py-2 text-left hover:border-indigo-300 hover:bg-indigo-50/40"
                      >
                        <FileText size={15} className="mt-0.5 shrink-0 text-indigo-600" aria-hidden />
                        <span className="min-w-0 text-xs">
                          <span className="font-semibold text-slate-800">[{i + 1}]</span>{" "}
                          <span className="text-slate-700">{c.title}</span>
                          <span className="block text-slate-500">
                            {c.section ? `§${c.section} ` : ""}
                            {pageLabel(c.page_start, c.page_end)}
                            {c.superseded ? " · superseded" : ""}
                          </span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {final.status !== "denied" && final.status !== "no_retrieval" && (
              <div className="mt-4 border-t border-slate-100 pt-3">
                <FeedbackBox answerId={final.answer_id} />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
