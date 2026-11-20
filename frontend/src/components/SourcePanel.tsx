import * as Dialog from "@radix-ui/react-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import { FileText, History, X } from "lucide-react";
import { useState } from "react";
import { pageLabel } from "../lib/citations";
import { highlightParts } from "../lib/highlight";
import type { Citation } from "../types";

interface Props {
  citations: Citation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
}

const TAB = "rounded-md px-3 py-1.5 text-xs font-medium text-slate-600 data-[state=active]:bg-indigo-50 data-[state=active]:text-indigo-800";

/** The selected source as a side sheet (Radix Dialog: focus stays inside, Esc closes):
 * title, section, page and the preview with the cited passage highlighted; a second tab
 * lists all sources of the answer. */
export function SourcePanel({ citations, activeId, onSelect, onClose }: Props) {
  const [tab, setTab] = useState("passage");
  const active = citations.find((c) => c.chunk_id === activeId) ?? citations[0];
  if (!active) return null;
  const parts = highlightParts(active.preview, active.cited_text);
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-900/10" />
        <Dialog.Content
          aria-label="Source details"
          className="fixed inset-y-0 right-0 z-50 flex w-[26rem] max-w-full flex-col border-l border-slate-200 bg-white shadow-xl focus:outline-none"
        >
          <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
            <Dialog.Title className="text-sm font-semibold text-slate-900">Source</Dialog.Title>
            <Dialog.Close aria-label="Close sources" className="rounded-md p-1 text-slate-500 hover:bg-slate-100">
              <X size={18} aria-hidden />
            </Dialog.Close>
          </div>
          <Dialog.Description className="sr-only">
            The passage the answer cites, and the answer's other sources.
          </Dialog.Description>

          <Tabs.Root value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
            <Tabs.List aria-label="Source views" className="flex gap-1 border-b border-slate-100 px-5 py-2">
              <Tabs.Trigger value="passage" className={TAB}>Cited passage</Tabs.Trigger>
              <Tabs.Trigger value="all" className={TAB}>All sources ({citations.length})</Tabs.Trigger>
            </Tabs.List>

            <Tabs.Content value="passage" className="flex-1 overflow-y-auto px-5 py-4">
              <div className="mb-1 text-xs font-medium text-indigo-700">
                [{citations.indexOf(active) + 1}] {active.chunk_id}
              </div>
              <h3 className="text-sm font-semibold leading-snug text-slate-900">{active.title}</h3>
              <p className="mt-1 text-xs text-slate-500">
                {active.section ? `§${active.section} ${active.section_title ?? ""}` : ""}
                {active.section && active.page_start ? " · " : ""}
                {pageLabel(active.page_start, active.page_end)}
              </p>
              {active.superseded && (
                <p className="mt-2 inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                  <History size={12} aria-hidden /> Superseded version
                </p>
              )}
              <blockquote className="mt-4 whitespace-pre-line border-l-4 border-indigo-200 bg-slate-50 px-4 py-3 text-sm leading-relaxed text-slate-700">
                {parts ? (
                  <>
                    {parts[0]}
                    <mark aria-label="Cited passage" className="rounded bg-yellow-100 px-0.5 text-slate-900">{parts[1]}</mark>
                    {parts[2]}
                  </>
                ) : active.preview}
              </blockquote>
              {!parts && (
                <p className="mt-2 text-[11px] text-slate-400">The exact supporting sentences could not be located in this extract.</p>
              )}
            </Tabs.Content>

            <Tabs.Content value="all" className="flex-1 overflow-y-auto px-5 py-4">
              <ul className="space-y-1">
                {citations.map((c, i) => (
                  <li key={c.chunk_id}>
                    <button
                      type="button"
                      onClick={() => { onSelect(c.chunk_id); setTab("passage"); }}
                      className={`flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs ${
                        c.chunk_id === active.chunk_id ? "bg-indigo-50 text-indigo-900" : "text-slate-600 hover:bg-slate-50"
                      }`}
                    >
                      <FileText size={13} className="mt-0.5 shrink-0" aria-hidden />
                      <span>
                        <span className="font-medium">[{i + 1}]</span> {c.title}
                        {c.page_start ? `, ${pageLabel(c.page_start, c.page_end)}` : ""}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </Tabs.Content>
          </Tabs.Root>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
