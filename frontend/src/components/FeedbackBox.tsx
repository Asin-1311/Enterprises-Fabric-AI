import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { sendFeedback } from "../api";

interface Props {
  answerId: number;
}

export function FeedbackBox({ answerId }: Props) {
  const [rating, setRating] = useState<"helpful" | "not_helpful" | null>(null);
  const [correction, setCorrection] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");

  async function submit(chosen: "helpful" | "not_helpful", text: string | null) {
    setState("saving");
    try {
      await sendFeedback(answerId, chosen, text);
      setState("saved");
    } catch {
      setState("error");
    }
  }

  if (state === "saved") return <p className="text-xs text-emerald-700">Thank you, your feedback was recorded.</p>;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <span>Was this helpful?</span>
        <button
          type="button" aria-label="Helpful" disabled={state === "saving"}
          onClick={() => { setRating("helpful"); void submit("helpful", null); }}
          className="rounded-md p-1.5 hover:bg-emerald-50 hover:text-emerald-700"
        >
          <ThumbsUp size={15} aria-hidden />
        </button>
        <button
          type="button" aria-label="Not helpful"
          onClick={() => setRating("not_helpful")}
          className={`rounded-md p-1.5 hover:bg-rose-50 hover:text-rose-700 ${rating === "not_helpful" ? "bg-rose-50 text-rose-700" : ""}`}
        >
          <ThumbsDown size={15} aria-hidden />
        </button>
        {state === "error" && <span className="text-rose-600">Could not save feedback.</span>}
      </div>
      {rating === "not_helpful" && (
        <div className="flex gap-2">
          <textarea
            aria-label="Correction (optional)"
            value={correction}
            onChange={(e) => setCorrection(e.target.value)}
            placeholder="What was wrong or missing? (optional)"
            rows={2}
            className="flex-1 rounded-md border border-slate-200 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-200"
          />
          <button
            type="button" disabled={state === "saving"}
            onClick={() => void submit("not_helpful", correction.trim() || null)}
            className="self-end rounded-md bg-slate-800 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-700"
          >
            Send
          </button>
        </div>
      )}
    </div>
  );
}
