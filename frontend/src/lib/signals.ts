// Implicit signals (M16b, CLAUDE.md sections 8 and 11). The UI reports how people react to
// an answer; the routing bandit turns them into small rewards (app/adaptive/routing_policy.py):
//   copy            the user copied the answer             (a good sign)
//   citation_click  the user opened one of its sources     (a good sign)
//   follow_up       another question within 5 minutes      (it may have been incomplete)
//   rephrase        the same question reworded, at once    (it missed the question)
// Each kind is sent at most once per answer, so repeated clicks do not count many times.

export type SignalKind = "copy" | "citation_click" | "follow_up" | "rephrase";

export const FOLLOW_UP_MS = 5 * 60_000;
export const REPHRASE_MS = 2 * 60_000;
export const REPHRASE_SIMILARITY = 0.5;

/** The previous answer in this conversation, for follow-up and rephrase. */
export interface LastAnswer {
  answerId: number;
  question: string;
  answeredAt: number; // ms since the epoch
}

function words(text: string): Set<string> {
  return new Set(text.toLowerCase().match(/[a-z0-9]{3,}/g) ?? []);
}

/** Share of words two questions have in common (Jaccard similarity, 0 to 1). */
export function similarity(a: string, b: string): number {
  const x = words(a);
  const y = words(b);
  if (!x.size || !y.size) return 0;
  let shared = 0;
  for (const w of x) if (y.has(w)) shared += 1;
  return shared / (x.size + y.size - shared);
}

/** What asking `question` at `now` says about the previous answer, if anything. */
export function signalForNextQuestion(
  previous: LastAnswer | null,
  question: string,
  now: number,
): { answerId: number; kind: SignalKind } | null {
  if (!previous) return null;
  const elapsed = now - previous.answeredAt;
  if (elapsed < 0 || elapsed > FOLLOW_UP_MS) return null;
  const kind = elapsed <= REPHRASE_MS && similarity(previous.question, question) >= REPHRASE_SIMILARITY
    ? "rephrase"
    : "follow_up";
  return { answerId: previous.answerId, kind };
}

/** Sends each (answer, kind) once. */
export class SignalSender {
  private sent = new Set<string>();

  constructor(private post: (answerId: number, kind: SignalKind) => Promise<void>) {}

  send(answerId: number, kind: SignalKind): void {
    const key = `${answerId}:${kind}`;
    if (this.sent.has(key)) return;
    this.sent.add(key);
    void this.post(answerId, kind);
  }
}
