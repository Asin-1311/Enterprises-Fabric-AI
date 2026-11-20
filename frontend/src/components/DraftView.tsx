import { Loader2 } from "lucide-react";
import { stripCitations } from "../lib/citations";
import { draftSegments, type SegmentState } from "../lib/draft";
import type { ClaimVerdict } from "../types";
import { CheckThisPoint } from "./Badges";

const STYLE: Record<SegmentState, string> = {
  pending: "text-slate-400", // written, not yet checked
  supported: "text-slate-800",
  flagged: "text-slate-800",
  removed: "hidden", // a removed claim disappears
};

interface Props {
  draft: string;
  verdicts: ClaimVerdict[];
}

/** The draft as it is written, muted, then sentence by sentence as each claim is checked.
 * Removed claims disappear; flagged ones are marked "check this point". */
export function DraftView({ draft, verdicts }: Props) {
  const segments = draftSegments(draft, verdicts);
  return (
    <div aria-label="Draft answer" className="mt-3">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-slate-500" role="status">
        <Loader2 size={13} className="animate-spin" aria-hidden /> Checking…
      </p>
      <p className="whitespace-pre-wrap text-[15px] leading-relaxed">
        {segments.map((s, i) => (
          <span key={i} data-state={s.state} className={STYLE[s.state]}>
            {stripCitations(s.text)}
            {s.state === "flagged" && <> <CheckThisPoint /></>}
          </span>
        ))}
      </p>
    </div>
  );
}
