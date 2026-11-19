// Types mirroring the FastAPI schemas in app/api/schemas.py.

export interface User {
  user_id: string;
  name: string;
  role: "staff" | "manager" | "partner";
  email: string | null;
  engagements: string[];
}

/** Returned by sign-in and registration (the token is shown once). */
export interface SessionInfo {
  token: string;
  expires_at: string;
  user: User;
}

export interface Citation {
  chunk_id: string;
  doc_id: string;
  title: string;
  section: string | null;
  section_title: string | null;
  page_start: number | null;
  page_end: number | null;
  superseded: boolean;
  preview: string;
}

export type AnswerStatus =
  | "answered"
  | "abstained"
  | "not_found"
  | "denied"
  | "no_retrieval"
  | "restricted_unavailable"; // M13a: restricted material and no private model

export interface FinalPayload {
  query_id: number;
  answer_id: number;
  status: AnswerStatus;
  text: string;
  confidence: number | null;
  claims_supported: number;
  claims_factual: number;
  removed_claims: number;
  superseded_involved: boolean;
  injection_flagged_chunks: number;
  citations: Citation[];
  seconds_to_answer: number | null; // null when reopened from history
}

/** One past question of the signed-in user (sidebar History). */
export interface HistoryItem {
  query_id: number;
  question: string;
  status: AnswerStatus;
  asked_at: string; // ISO date-time, UTC
}

/** One event from POST /api/v1/query (Server-Sent Events). */
export type QueryEvent =
  | { event: "progress"; data: { step: string; [key: string]: unknown } }
  | { event: "token"; data: { text: string } }
  | { event: "final"; data: FinalPayload }
  | { event: "error"; data: { message: string } };
