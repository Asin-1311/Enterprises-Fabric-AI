// Calls to the FastAPI backend (/api/v1). In development Vite forwards /api to port 8000;
// in the built app FastAPI serves this UI itself, so relative URLs work in both cases.
//
// Signing in returns a token; every later call sends it as `Authorization: Bearer ...`.
// The API decides who the user is from that token alone (never from the request body).
// A 401 answer means the session ended, so the app returns to the sign-in screen.

import { SSEParser } from "./lib/sse";
import { clearToken, currentToken } from "./lib/session";
import type { SignalKind } from "./lib/signals";
import type { FinalPayload, HistoryItem, QueryEvent, SessionInfo, User } from "./types";

const API = "/api/v1";
export const API_DOWN_MESSAGE = "The API is not reachable. Start it with scripts\\run_demo.bat.";

/** An error message from the API that is safe to show to the user. */
export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

let onSessionEnded: () => void = () => {};

/** Called when the API rejects the token (expired, signed out elsewhere, reset password). */
export function setSessionEndedHandler(handler: () => void): void {
  onSessionEnded = handler;
}

/** The API's own message (FastAPI `detail`), or `fallback`. */
async function messageFrom(response: Response, fallback: string): Promise<string> {
  try {
    const detail = (await response.json())?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg).replace(/^Value error, /, "");
  } catch {
    // not JSON
  }
  return fallback;
}

async function call(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  const token = currentToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API}${path}`, { ...init, headers });
  if (response.status === 401 && token) {
    clearToken();
    onSessionEnded();
  }
  return response;
}

async function callJson<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const response = await call(path, init);
  if (!response.ok) throw new ApiError(await messageFrom(response, fallback), response.status);
  return (await response.json()) as T;
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

// ------------------------------------------------------------------------------ sign-in

/** What the sign-in screen may offer (self-registration is off unless the firm enables it). */
export const getAuthConfig = () =>
  callJson<{ self_registration: boolean }>("/auth/config", {}, "Sign-in settings unavailable.");

export const login = (email: string, password: string) =>
  callJson<SessionInfo>("/auth/login", post({ email, password }), "Sign-in failed.");

export const register = (name: string, email: string, password: string) =>
  callJson<SessionInfo>("/auth/register", post({ name, email, password }), "The account could not be created.");

export const forgotPassword = (email: string) =>
  callJson<{ message: string }>("/auth/forgot-password", post({ email }), "The request could not be sent.");

export const resetPassword = (token: string, password: string) =>
  callJson<{ reset: boolean }>("/auth/reset-password", post({ token, password }), "The password could not be changed.");

export const getMe = () => callJson<User>("/auth/me", {}, "Not signed in.");

export async function logout(): Promise<void> {
  try {
    await call("/auth/logout", { method: "POST" });
  } finally {
    clearToken();
  }
}

// ------------------------------------------------------------------------------ app data

/** The signed-in user's own past questions, newest first. */
export const getHistory = () => callJson<HistoryItem[]>("/history", {}, "History unavailable.");

/** Reopen a past answer (same shape as the streamed `final` event). */
export const getHistoryAnswer = (queryId: number) =>
  callJson<FinalPayload>(`/history/${queryId}`, {}, "This answer could not be opened.");

/** POST a question and call `onEvent` for every streamed event: progress steps, the draft
 * as it is written (draft_token), one claim_verdict per checked claim, the verified answer
 * (token), then final. */
export async function streamQuery(
  text: string,
  onEvent: (event: QueryEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await call("/query", { ...post({ text }), signal });
  if (!response.ok || !response.body) {
    throw new Error(response.status === 401
      ? "Your session has ended. Sign in again."
      : `The question could not be sent (${response.status}).`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SSEParser();
  const emit = (messages: { event: string; data: string }[]) => {
    for (const m of messages) {
      if (["progress", "draft_token", "claim_verdict", "token", "final", "error"].includes(m.event)) {
        onEvent({ event: m.event, data: JSON.parse(m.data) } as QueryEvent);
      }
    }
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    emit(parser.push(decoder.decode(value, { stream: true })));
  }
  emit(parser.flush());
}

export async function sendFeedback(
  answerId: number,
  rating: "helpful" | "not_helpful",
  correction: string | null,
): Promise<void> {
  await callJson("/feedback", post({ answer_id: answerId, rating, correction }), "Feedback not saved.");
}

/** An implicit signal about one of the user's answers (copy, citation click, follow-up,
 * rephrase). It only informs learning, so a failure is ignored. */
export async function sendSignal(answerId: number, kind: SignalKind): Promise<void> {
  try {
    await call("/feedback/signals", post({ answer_id: answerId, kind }));
  } catch {
    // never disturb the user over a signal
  }
}
