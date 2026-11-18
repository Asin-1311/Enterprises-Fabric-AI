import { FileText, History, X } from "lucide-react";
import { pageLabel } from "../lib/citations";
import type { Citation } from "../types";

interface Props {
  citations: Citation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
}

/** Slide-over panel: the selected source (title, section, page, preview) and the others. */
export function SourcePanel({ citations, activeId, onSelect, onClose }: Props) {
  const active = citations.find((c) => c.chunk_id === activeId) ?? citations[0];
  if (!active) return null;
  return (
    <aside
      className="flex h-full w-[26rem] shrink-0 flex-col border-l border-slate-200 bg-white shadow-xl"
      aria-label="Source details"
    >
      <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
        <h2 className="text-sm font-semibold text-slate-900">Source</h2>
        <button type="button" onClick={onClose} aria-label="Close sources" className="rounded-md p-1 text-slate-500 hover:bg-slate-100">
          <X size={18} aria-hidden />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-5 py-4">
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
          {active.preview}
        </blockquote>

        {citations.length > 1 && (
          <>
            <h4 className="mb-2 mt-6 text-xs font-semibold uppercase tracking-wide text-slate-500">All sources</h4>
            <ul className="space-y-1">
              {citations.map((c, i) => (
                <li key={c.chunk_id}>
                  <button
                    type="button"
                    onClick={() => onSelect(c.chunk_id)}
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
          </>
        )}
      </div>
    </aside>
  );
}
