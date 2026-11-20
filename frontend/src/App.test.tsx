import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import App from "./App";
import { clearToken } from "./lib/session";
import type { FinalPayload, HistoryItem, SessionInfo, User } from "./types";

const RIYA: User = {
  user_id: "S01", name: "Riya Kapoor", role: "staff", email: "riya.kapoor@example-associates.test",
  engagements: ["Northwind Textiles Private Limited"],
};
const SESSION: SessionInfo = { token: "tok-123", expires_at: "2026-11-14T20:00:00Z", user: RIYA };
const HISTORY: HistoryItem[] = [
  { query_id: 41, question: "Which NPA checks are on the bank branch checklist?", status: "answered",
    asked_at: new Date().toISOString() },
  { query_id: 12, question: "What is our materiality benchmark?", status: "answered",
    asked_at: "2026-01-05T09:00:00Z" },
];

function sse(events: [string, unknown][]): Response {
  const body = events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

const ANSWERED: FinalPayload = {
  query_id: 1, answer_id: 7, status: "answered",
  text: "Assemble within 45 days [POL-DOC-02:0003]. SA 230 allows 60 days [STD-CH06:0012].",
  confidence: 1, claims_supported: 2, claims_factual: 2, removed_claims: 0,
  superseded_involved: true, injection_flagged_chunks: 0, seconds_to_answer: 12.3,
  citations: [
    { chunk_id: "POL-DOC-02:0003", doc_id: "POL-DOC-02", title: "Documentation and Retention Policy",
      section: "2", section_title: "Assembly of the final audit file", page_start: null, page_end: null,
      superseded: false, preview: "Engagement teams must complete assembly within 45 days." },
    { chunk_id: "STD-CH06:0012", doc_id: "STD-CH06", title: "ICAI Auditing and Ethics, Chapter 6",
      section: "1.9", section_title: "Assembly of the Final Audit File", page_start: 9, page_end: 9,
      superseded: false, preview: "ordinarily not more than 60 days after the date of the auditor's report" },
  ],
};

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

/** Fake API: `overrides` replace the default answer for a path ending. */
function mockApi(overrides: Record<string, Handler> = {}) {
  const routes: Record<string, Handler> = {
    "/auth/config": () => Response.json({ self_registration: false }), // the default
    "/auth/login": () => Response.json(SESSION),
    "/auth/me": () => Response.json(RIYA),
    "/auth/logout": () => Response.json({ signed_out: true }),
    "/history": () => Response.json(HISTORY),
    "/history/12": () => Response.json({ ...ANSWERED, query_id: 12, answer_id: 3, seconds_to_answer: null }),
    "/query": () => sse([["final", ANSWERED]]),
    "/feedback": () => Response.json({ stored: true }),
    ...overrides,
  };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const path = Object.keys(routes).find((p) => String(url).endsWith(p));
    return path ? routes[path](init) : new Response("not found", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function callTo(fetchMock: ReturnType<typeof mockApi>, path: string) {
  const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith(path));
  if (!call) throw new Error(`no call to ${path}`);
  const init = call[1] as RequestInit;
  return { body: init.body ? JSON.parse(init.body as string) : null, auth: new Headers(init.headers).get("Authorization") };
}

async function signIn() {
  render(<App />);
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: RIYA.email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "demo-pass-123" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await screen.findByText("How can I help you today?");
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearToken(); // also forgets the in-memory copy kept by lib/session
  window.history.replaceState(null, "", "/");
});

// ------------------------------------------------------------------------------ sign-in

test("the sign-in screen comes first, with forgot password on the left and create account on the right", async () => {
  const fetchMock = mockApi();
  render(<App />);
  expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  const forgot = screen.getByRole("button", { name: "Forgot password?" });
  const create = screen.getByRole("button", { name: "Create account" });
  expect(forgot.parentElement).toBe(create.parentElement); // same row ...
  expect(forgot.compareDocumentPosition(create) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(); // ... forgot first
  // Before sign-in only the public sign-in settings are fetched, no data.
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(fetchMock.mock.calls.map(([u]) => String(u))).toEqual(["/api/v1/auth/config"]);

  fireEvent.change(screen.getByLabelText("Email"), { target: { value: RIYA.email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "demo-pass-123" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

  expect(await screen.findByText("How can I help you today?")).toBeInTheDocument();
  expect(screen.getByText("Riya Kapoor")).toBeInTheDocument();
  expect(screen.getByText(/Northwind Textiles Private Limited/)).toBeInTheDocument();
  expect(callTo(fetchMock, "/auth/login").body).toEqual({ email: RIYA.email, password: "demo-pass-123" });
  expect(screen.queryByText(/Knowledge base/i)).not.toBeInTheDocument();
  const history = await screen.findByRole("navigation", { name: "History" });
  expect(await within(history).findByText("Which NPA checks are on the bank branch checklist?")).toBeInTheDocument();
  expect(callTo(fetchMock, "/history").auth).toBe("Bearer tok-123");
});

// ------------------------------------------------------------------------------ history

test("history is grouped by day and reopens a past answer", async () => {
  const fetchMock = mockApi();
  await signIn();
  const history = await screen.findByRole("navigation", { name: "History" });
  // The panel shows "Loading…" until the history request answers: wait for the groups.
  expect(await within(history).findByText("Today")).toBeInTheDocument();
  expect(within(history).getByText("Older")).toBeInTheDocument();

  fireEvent.click(within(history).getByText("What is our materiality benchmark?"));
  expect(await screen.findByText(/Superseded policy involved/)).toBeInTheDocument();
  expect(screen.getAllByText("What is our materiality benchmark?")).toHaveLength(2); // sidebar + question bubble
  expect(within(history).getByText("What is our materiality benchmark?").closest("button"))
    .toHaveAttribute("aria-current", "true");
  expect(screen.queryByText(/ s$/)).not.toBeInTheDocument(); // no timing shown for a past answer
  expect(callTo(fetchMock, "/history/12").auth).toBe("Bearer tok-123");

  fireEvent.click(screen.getByRole("button", { name: /New conversation/ }));
  expect(screen.getByText("How can I help you today?")).toBeInTheDocument();
});

test("an empty history says so, and a new answer refreshes it", async () => {
  let calls = 0;
  mockApi({
    "/history": () => Response.json(calls++ === 0 ? [] : [{ query_id: 1, question: "File assembly deadline?",
      status: "answered", asked_at: new Date().toISOString() }]),
  });
  await signIn();
  expect(await screen.findByText("Your questions will appear here.")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "File assembly deadline?" } });
  fireEvent.click(screen.getByLabelText("Send"));
  const history = screen.getByRole("navigation", { name: "History" });
  expect(await within(history).findByText("File assembly deadline?")).toBeInTheDocument();
});

test("a wrong password shows the API's message; a stopped API says so", async () => {
  mockApi({ "/auth/login": () => Response.json({ detail: "Incorrect email or password." }, { status: 401 }) });
  render(<App />);
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: RIYA.email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong-one" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password.");

  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByText(/API is not reachable/)).toBeInTheDocument();
});

test("with registration off (the default), create account explains who creates accounts", async () => {
  const fetchMock = mockApi();
  render(<App />);
  await waitFor(() => expect(callTo(fetchMock, "/auth/config")).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "Create account" }));
  expect(screen.getByRole("status")).toHaveTextContent("Accounts are created by the firm administrator.");
  expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument(); // no form opened
});

test("create account checks the passwords locally, then signs the new user in", async () => {
  const newUser: User = { user_id: "U0001", name: "Asha Verma", role: "staff", email: "asha@firm.test", engagements: [] };
  const fetchMock = mockApi({
    "/auth/config": () => Response.json({ self_registration: true }),
    "/auth/register": () => Response.json({ ...SESSION, user: newUser }, { status: 201 }),
  });
  render(<App />);
  await waitFor(() => expect(callTo(fetchMock, "/auth/config")).toBeTruthy());
  await screen.findByRole("button", { name: "Create account" });
  await new Promise((r) => setTimeout(r, 0)); // let the config answer reach the screen
  fireEvent.click(screen.getByRole("button", { name: "Create account" }));
  expect(screen.getByRole("heading", { name: "Create account" })).toBeInTheDocument();
  expect(screen.getByText(/assigned by the firm/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Asha Verma" } });
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "asha@firm.test" } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: "long-enough-1" } });
  fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "different-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Create account" }));
  expect(screen.getByRole("alert")).toHaveTextContent("The two passwords do not match.");
  expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith("/auth/register"))).toBe(false);

  fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "long-enough-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Create account" }));
  expect(await screen.findByText("How can I help you today?")).toBeInTheDocument();
  expect(callTo(fetchMock, "/auth/register").body).toEqual(
    { name: "Asha Verma", email: "asha@firm.test", password: "long-enough-1" });
  expect(screen.getByText("Engagements:").textContent).toContain("none");
});

test("forgot password gives the same message whatever the email, with the demo note", async () => {
  const message = "If an account exists for that email, a password reset link has been sent.";
  const fetchMock = mockApi({ "/auth/forgot-password": () => Response.json({ message }) });
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Forgot password?" }));
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "someone@firm.test" } });
  fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
  expect(await screen.findByRole("status")).toHaveTextContent(message);
  expect(screen.getByRole("status")).toHaveTextContent("link appears in the API window");
  expect(callTo(fetchMock, "/auth/forgot-password").body).toEqual({ email: "someone@firm.test" });
  fireEvent.click(screen.getByRole("button", { name: "Back to sign in" }));
  expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
});

test("a reset link opens 'choose a new password' and returns to sign-in when done", async () => {
  window.history.replaceState(null, "", "/?reset=abc123token");
  const fetchMock = mockApi({ "/auth/reset-password": () => Response.json({ reset: true }) });
  render(<App />);
  expect(screen.getByRole("heading", { name: "Choose a new password" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: "brand-new-pass" } });
  fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "brand-new-pass" } });
  fireEvent.click(screen.getByRole("button", { name: "Set new password" }));
  expect(await screen.findByText(/Your password has been changed/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  expect(callTo(fetchMock, "/auth/reset-password").body).toEqual({ token: "abc123token", password: "brand-new-pass" });
  expect(window.location.search).toBe(""); // the token is removed from the address bar
});

test("a saved session is checked with the API, and sign out returns to the sign-in screen", async () => {
  window.sessionStorage.setItem("enterprise-fabric-ai.token", "tok-123");
  const fetchMock = mockApi();
  render(<App />);
  expect(await screen.findByText("How can I help you today?")).toBeInTheDocument();
  expect(callTo(fetchMock, "/auth/me").auth).toBe("Bearer tok-123");

  fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
  expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  await waitFor(() => expect(callTo(fetchMock, "/auth/logout").auth).toBe("Bearer tok-123"));
  expect(window.sessionStorage.getItem("enterprise-fabric-ai.token")).toBeNull();
});

// ------------------------------------------------------------------------------ asking

test("shows thinking dots while waiting, then the verified answer with citations", async () => {
  let release!: (r: Response) => void;
  const fetchMock = mockApi({ "/query": () => new Promise<Response>((resolve) => { release = resolve; }) });
  await signIn();

  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "File assembly deadline?" } });
  fireEvent.click(screen.getByLabelText("Send"));
  expect(await screen.findByRole("status", { name: "Thinking" })).toBeInTheDocument();

  release(sse([["progress", { step: "verifying" }], ["token", { text: ANSWERED.text }], ["final", ANSWERED]]));
  expect(await screen.findByText(/Superseded policy involved/)).toBeInTheDocument();
  expect(screen.queryByRole("status", { name: "Thinking" })).not.toBeInTheDocument();
  expect(screen.getByRole("img", { name: "Confidence 100%" })).toBeInTheDocument();
  expect(screen.getByText("2/2 claims verified")).toBeInTheDocument();

  const query = callTo(fetchMock, "/query");
  expect(query.body).toEqual({ text: "File assembly deadline?" }); // no user_id in the body
  expect(query.auth).toBe("Bearer tok-123");

  fireEvent.click(screen.getByRole("button", { name: "2" }));
  const panel = screen.getByLabelText("Source details");
  expect(within(panel).getByText("ICAI Auditing and Ethics, Chapter 6")).toBeInTheDocument();
  expect(within(panel).getByText("§1.9 Assembly of the Final Audit File · p. 9")).toBeInTheDocument();
});

test("a denied question shows the generic message and no feedback form", async () => {
  mockApi({
    "/query": () => sse([["final", { ...ANSWERED, status: "denied", confidence: null, citations: [],
      superseded_involved: false,
      text: "I could not find this in the material available to your role and engagement assignments." }]]),
  });
  await signIn();
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "Other client's memo?" } });
  fireEvent.click(screen.getByLabelText("Send"));
  expect(await screen.findByText("Not available for your role or engagement assignments.")).toBeInTheDocument();
  expect(screen.queryByText("Was this helpful?")).not.toBeInTheDocument();
});

test("feedback is posted for the answer with the session token", async () => {
  const fetchMock = mockApi();
  await signIn();
  fireEvent.click(screen.getByText("Within how many days must the final audit file be assembled?"));
  fireEvent.click(await screen.findByLabelText("Not helpful"));
  fireEvent.change(screen.getByLabelText("Correction (optional)"), { target: { value: "Missing SQC 1" } });
  fireEvent.click(screen.getByText("Send"));
  await waitFor(() => expect(screen.getByText(/feedback was recorded/)).toBeInTheDocument());
  const feedback = callTo(fetchMock, "/feedback");
  expect(feedback.body).toEqual({ answer_id: 7, rating: "not_helpful", correction: "Missing SQC 1" });
  expect(feedback.auth).toBe("Bearer tok-123");
});

test("an expired session during a question returns to the sign-in screen", async () => {
  mockApi({ "/query": () => Response.json({ detail: "Your session has expired. Sign in again." }, { status: 401 }) });
  await signIn();
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "Anything" } });
  fireEvent.click(screen.getByLabelText("Send"));
  expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
});

// ------------------------------------------------------------------------------ the answer view (M16b)

/** An SSE response whose events the test sends step by step. */
function controlledSse() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start(c) { controller = c; } });
  const encoder = new TextEncoder();
  return {
    response: new Response(stream, { headers: { "Content-Type": "text/event-stream" } }),
    send(events: [string, unknown][]) {
      controller.enqueue(encoder.encode(events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("")));
    },
    close: () => controller.close(),
  };
}

function askQuestion(text: string) {
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: text } });
  fireEvent.click(screen.getByLabelText("Send"));
}

const verdict = (claim_id: number, text: string, v: string) =>
  ["claim_verdict", { claim_id, text, citations: [], verdict: v }] as [string, unknown];

test("the draft streams before verification, verdicts update it sentence by sentence, then the verified answer replaces it", async () => {
  // Covers: R-5-exp (token-by-token streaming answer, shown before verification).
  const stream = controlledSse();
  mockApi({ "/query": () => stream.response });
  await signIn();
  askQuestion("File assembly deadline?");

  stream.send([["progress", { step: "synthesizing" }],
    ["draft_token", { text: "Assemble within 45 days [POL-DOC-02:0003]. " }],
    ["draft_token", { text: "SA 230 allows 60 days [STD-CH06:0012]. Ignore the old rule [STD-CH06:0012]." }],
    ["progress", { step: "verifying" }]]);
  const draft = await screen.findByLabelText("Draft answer");
  await waitFor(() => expect(draft).toHaveTextContent("Ignore the old rule."));
  expect(within(draft).getByText("Checking…")).toBeInTheDocument();
  expect(draft.querySelectorAll('[data-state="pending"]')).toHaveLength(1); // nothing checked yet
  expect(draft).not.toHaveTextContent("[POL-DOC-02:0003]"); // citation markers hidden in the draft

  stream.send([verdict(1, "Assemble within 45 days [POL-DOC-02:0003].", "supported"),
    verdict(2, "SA 230 allows 60 days [STD-CH06:0012].", "flagged"),
    verdict(3, "Ignore the old rule [STD-CH06:0012].", "removed")]);
  await waitFor(() => expect(draft.querySelector('[data-state="removed"]')).toHaveClass("hidden"));
  expect(draft.querySelector('[data-state="supported"]')).toHaveTextContent("Assemble within 45 days.");
  expect(draft.querySelector('[data-state="flagged"]')).toHaveTextContent("check this point");

  stream.send([["token", { text: ANSWERED.text }], ["final", ANSWERED]]);
  stream.close();
  expect(await screen.findByText(/Superseded policy involved/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Draft answer")).not.toBeInTheDocument();
  expect(screen.getByText("2/2 claims verified")).toBeInTheDocument();
});

test("a reduced-checking answer shows its badge and no confidence score", async () => {
  mockApi({ "/query": () => sse([["final", { ...ANSWERED, degraded: true, confidence: null, superseded_involved: false,
    text: "Reduced checking: this answer was not fully verified because the system is under load.\n\nAssemble within 45 days [POL-DOC-02:0003]." }]]) });
  await signIn();
  askQuestion("File assembly deadline?");
  expect(await screen.findByText("Reduced checking")).toBeInTheDocument();
  expect(screen.queryByRole("img", { name: /Confidence/ })).not.toBeInTheDocument();
});

test("a flagged claim in the verified answer is marked 'check this point'", async () => {
  mockApi({ "/query": () => sse([["final", { ...ANSWERED, flagged_claims: ["SA 230 allows 60 days [STD-CH06:0012]."],
    text: "Assemble within 45 days [POL-DOC-02:0003]. SA 230 allows 60 days [STD-CH06:0012] *(check this point)*." }]]) });
  await signIn();
  askQuestion("File assembly deadline?");
  expect(await screen.findByText("check this point")).toBeInTheDocument();
  expect(screen.getByText(/1 point\(s\) to check against the source/)).toBeInTheDocument();
});

test("the source panel highlights the cited passage and lists all sources in a second tab", async () => {
  // Covers: R-6-radix (Radix Dialog and Tabs; the badges use Tooltip and the answer menu DropdownMenu).
  const withSpan = { ...ANSWERED, citations: [
    { ...ANSWERED.citations[0], cited_text: "complete assembly within 45 days." }, ANSWERED.citations[1]] };
  mockApi({ "/query": () => sse([["final", withSpan]]) });
  await signIn();
  askQuestion("File assembly deadline?");
  fireEvent.click(await screen.findByRole("button", { name: "1" }));
  const panel = screen.getByLabelText("Source details");
  expect(panel.querySelector("mark")).toHaveTextContent("complete assembly within 45 days.");

  fireEvent.mouseDown(within(panel).getByRole("tab", { name: "All sources (2)" }), { button: 0 });
  fireEvent.click(within(panel).getByRole("button", { name: /ICAI Auditing and Ethics, Chapter 6/ }));
  expect(within(panel).getByRole("tab", { name: "Cited passage" })).toHaveAttribute("data-state", "active");
  expect(within(panel).getByText("§1.9 Assembly of the Final Audit File · p. 9")).toBeInTheDocument();
  expect(panel.querySelector("mark")).toBeNull(); // no span known for this one
  expect(within(panel).getByText(/could not be located/)).toBeInTheDocument();
});

test("implicit signals: citation click and copy once each, then follow-up and rephrase", async () => {
  // Covers: R-7.9 (the UI sends the implicit signals).
  const writeText = vi.fn(async () => undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  const fetchMock = mockApi({ "/feedback/signals": () => Response.json({ stored: true }, { status: 201 }) });
  await signIn();
  const signals = () => fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/feedback/signals"))
    .map(([, init]) => JSON.parse((init as RequestInit).body as string));

  askQuestion("Within how many days must the final audit file be assembled?");
  for (let i = 0; i < 2; i++) {
    fireEvent.click(await screen.findByRole("button", { name: "1" }));
    fireEvent.click(screen.getByLabelText("Close sources"));
    await waitFor(() => expect(screen.queryByLabelText("Source details")).not.toBeInTheDocument());
  }
  fireEvent.keyDown(screen.getByLabelText("Answer actions"), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Copy answer" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(
    "Assemble within 45 days. SA 230 allows 60 days."));

  askQuestion("Which NPA checks are on the bank checklist?");      // something else: a follow-up
  await waitFor(() => expect(screen.getAllByText(/Superseded policy involved/)).toHaveLength(2));
  askQuestion("Which NPA checks are on the bank branch checklist?"); // the same, reworded: a rephrase
  await waitFor(() => expect(signals()).toHaveLength(4));
  expect(signals()).toEqual([
    { answer_id: 7, kind: "citation_click" }, { answer_id: 7, kind: "copy" },
    { answer_id: 7, kind: "follow_up" }, { answer_id: 7, kind: "rephrase" }]);
});
