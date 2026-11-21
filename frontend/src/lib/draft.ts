// The live draft (M16b, CLAUDE.md section 11). While the answer is being checked, the draft
// is shown muted, labelled "Checking…". Each `claim_verdict` event then changes the sentence
// it is about: supported sentences turn normal, removed ones disappear, flagged ones stay
// with a "check this point" marker. When the verified answer arrives it replaces the draft.
//
// A verdict carries the claim's text exactly as it appears in the draft, so each one is
// found in the draft in order; text no verdict has reached yet stays "pending" (muted).

import type { ClaimVerdict } from "../types";

export type SegmentState = "pending" | ClaimVerdict["verdict"];

export interface DraftSegment {
  text: string;
  state: SegmentState;
}

/** Split the draft into pending text and the claims the verifier has decided. */
export function draftSegments(draft: string, verdicts: ClaimVerdict[]): DraftSegment[] {
  const out: DraftSegment[] = [];
  let cursor = 0;
  for (const v of verdicts) {
    const claim = v.text.trim();
    const at = claim ? draft.indexOf(claim, cursor) : -1;
    if (at < 0) continue; // not found in order (e.g. text changed): it stays pending
    if (at > cursor) out.push({ text: draft.slice(cursor, at), state: "pending" });
    out.push({ text: claim, state: v.verdict });
    cursor = at + claim.length;
  }
  if (cursor < draft.length) out.push({ text: draft.slice(cursor), state: "pending" });
  return out;
}
