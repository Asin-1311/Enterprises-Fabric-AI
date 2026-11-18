import { Check, Loader2 } from "lucide-react";

// Labels for the agent steps the API reports as `progress` events.
export const STEP_LABELS: Record<string, string> = {
  understanding: "Understanding the question",
  routing: "Choosing sources",
  retrieving: "Searching the knowledge base",
  reranking: "Re-ranking results",
  followup_retrieval: "Searching again with different wording",
  compressing: "Selecting relevant sentences",
  synthesizing: "Writing a draft answer",
  verifying: "Checking every claim against its source",
  retrying_retrieval: "Confidence too low: searching once more",
};

interface Props {
  steps: string[];
  done: boolean;
}

export function ProgressTimeline({ steps, done }: Props) {
  if (!steps.length) return null;
  return (
    <ol className="space-y-1.5" aria-label="Progress">
      {steps.map((step, i) => {
        const finished = done || i < steps.length - 1;
        return (
          <li key={`${step}-${i}`} className="flex items-center gap-2 text-sm">
            {finished ? (
              <Check size={15} className="text-emerald-600" aria-hidden />
            ) : (
              <Loader2 size={15} className="animate-spin text-indigo-600" aria-hidden />
            )}
            <span className={finished ? "text-slate-500" : "font-medium text-slate-800"}>
              {STEP_LABELS[step] ?? step}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
