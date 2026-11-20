import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import App from "../App";
import { clearToken, saveToken } from "../lib/session";
import type { User } from "../types";

const ADMIN: User = { user_id: "A01", name: "Kiran Rao", role: "staff", email: "kiran.rao@example-associates.test",
  engagements: [], is_admin: true };
const STAFF: User = { ...ADMIN, user_id: "S01", name: "Riya Kapoor", is_admin: false };
const NOW = "2026-11-20T10:00:00";

type Handler = (init?: RequestInit) => Response;

/** Fake API keyed by "METHOD /path" (the path after /api/v1). */
function mockApi(user: User, extra: Record<string, Handler> = {}) {
  const routes: Record<string, Handler> = {
    "GET /auth/me": () => Response.json(user),
    "GET /history": () => Response.json([]),
    "GET /auth/config": () => Response.json({ self_registration: false }),
    "GET /sources": () => Response.json([{ id: "fs-faq", type: "filesystem", connector_config: { path: "data/corpus/faq" },
      owner: null, classification: "public_internal", status: "active", last_event_at: NOW, created_at: NOW }]),
    "POST /sources/fs-faq/pause": () => Response.json({ id: "fs-faq", status: "paused" }),
    "POST /sources": (init) => Response.json({ ...JSON.parse(String(init!.body)), owner: null,
      classification: "public_internal", status: "active", last_event_at: null, created_at: NOW }, { status: 201 }),
    "GET /admin/queries?limit=50": () => Response.json([{ query_id: 9, user_id: "S01", asked_at: NOW, intent: "factual",
      status: "answered", question: null, question_hidden: true }]),
    "GET /traces/9": () => Response.json([{ agent: "synthesis", model: "claude-sonnet-5-5", effort: "medium",
      tokens_in: 1200, tokens_out: 300, latency_ms: 4200, prompt_version_id: 2, policy_version_id: 1,
      content_hidden: true, input: null, output: null }]),
    "GET /admin/prompts": () => Response.json([
      { id: 1, agent: "synthesis", version: 1, template: "seed", eval_score: null, eval_report: { replay: "not required" },
        created_by: "system (seed)", created_at: NOW, activated_at: NOW, is_active: false },
      { id: 2, agent: "synthesis", version: 2, template: "new", eval_score: 1, eval_report: { replay: { passed: true } },
        created_by: "A01", created_at: NOW, activated_at: NOW, is_active: true },
      { id: 3, agent: "synthesis", version: 3, template: "draft", eval_score: null, eval_report: null,
        created_by: "A01", created_at: NOW, activated_at: null, is_active: false }]),
    "POST /admin/prompts/1/activate": () => Response.json({ action: "prompt_rolled_back", version: {} }),
    "GET /admin/routing-policies": () => Response.json([{ id: 1, version: 1, weights: { kind: "rules" }, created_by: "system",
      eval_report: { replay: "not required" }, activated_at: NOW, is_active: true }]),
    "GET /admin/classification-policies": () => Response.json([]),
    "POST /admin/classification-policies": (init) => Response.json({ policy: { id: 1, ...JSON.parse(String(init!.body)),
      active: true, created_by: "A01", created_at: NOW, deactivated_at: null }, reclassified: ["FAQ-01", "FAQ-02"] }, { status: 201 }),
    "GET /admin/agents/performance": () => Response.json({ from: NOW, to: NOW, answers: 12,
      answers_by_status: { answered: 10, not_found: 2 }, verification_steps: 10, verification_failure_rate: 0.2,
      degraded_rate: 0, cache_hit_rate: 0.25, estimated_cost_per_answer_usd: 0.0123, cost_basis: "estimate: token counts",
      agents: [{ agent: "synthesis", steps: 10, latency_p50_ms: 3100, latency_p95_ms: 5200, tokens_in: 9000, tokens_out: 2500 }] }),
    ...extra,
  };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(url).replace(/^\/api\/v1/, "")}`;
    return routes[key] ? routes[key](init) : new Response("not found", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const calls = (fetchMock: ReturnType<typeof mockApi>, method: string, path: string) =>
  fetchMock.mock.calls.filter(([u, i]) => (i?.method ?? "GET") === method && String(u).endsWith(path));

function openConsole() {
  saveToken("tok-admin");
  window.history.pushState(null, "", "/admin");
  render(<App />);
}

function tab(name: string) {
  fireEvent.mouseDown(screen.getByRole("tab", { name }), { button: 0 });
}

afterEach(() => {
  clearToken();
  window.history.pushState(null, "", "/");
  vi.unstubAllGlobals();
});

test("an administrator registers and pauses a source, inspects a trace and rolls back a prompt", async () => {
  // Covers: R-7.10 (admin console UI: register a source, inspect a trace, roll back a prompt).
  const fetchMock = mockApi(ADMIN);
  openConsole();
  expect(await screen.findByRole("heading", { name: "Admin console" })).toBeInTheDocument();

  // Sources: pause one, register another.
  const sources = await screen.findByRole("table", { name: "Sources" });
  fireEvent.click(within(sources).getByRole("button", { name: "Pause" }));
  expect(await screen.findByText("fs-faq paused.")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Source id"), { target: { value: "s3-policies" } });
  fireEvent.click(screen.getByRole("button", { name: "Register" }));
  expect(await screen.findByText("Source s3-policies registered.")).toBeInTheDocument();
  const sent = JSON.parse(String(calls(fetchMock, "POST", "/sources")[0][1]!.body));
  expect(sent).toMatchObject({ id: "s3-policies", type: "s3", connector_config: { bucket: "firm-documents" } });

  // Questions & traces: no question text for a non-partner, step timings shown.
  tab("Questions & traces");
  expect(await screen.findByText("hidden (partners only)")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Trace" }));
  const trace = await screen.findByRole("table", { name: "Trace steps" });
  expect(within(trace).getByText("4200 ms")).toBeInTheDocument();
  expect(screen.getByText(/Inputs and outputs are hidden/)).toBeInTheDocument();

  // Prompts: v1 served before (roll back), v3 has no replay (no button), v2 is active.
  tab("Prompts");
  const rollBack = await screen.findByRole("button", { name: "Roll back synthesis v1" });
  expect(screen.queryByRole("button", { name: /synthesis v3/ })).not.toBeInTheDocument();
  fireEvent.click(rollBack);
  expect(await screen.findByText("synthesis v1 is now active (rolled back).")).toBeInTheDocument();
  expect(calls(fetchMock, "POST", "/admin/prompts/1/activate")).toHaveLength(1);
});

test("classification rules and agent performance", async () => {
  const fetchMock = mockApi(ADMIN);
  openConsole();
  await screen.findByRole("heading", { name: "Admin console" });

  tab("Classification");
  expect(await screen.findByText("No rules yet.")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Source type"), { target: { value: "faq" } });
  fireEvent.change(screen.getByLabelText("Minimum classification"), { target: { value: "restricted" } });
  fireEvent.click(screen.getByRole("button", { name: "Add rule" }));
  expect(await screen.findByText("Rule added: 2 document(s) reclassified.")).toBeInTheDocument();
  expect(JSON.parse(String(calls(fetchMock, "POST", "/admin/classification-policies")[0][1]!.body)))
    .toEqual({ match: "source_type", pattern: "faq", classification: "restricted" });

  tab("Performance");
  expect(await screen.findByText("20.0%")).toBeInTheDocument(); // verification failure rate
  expect(screen.getByText("$0.0123")).toBeInTheDocument();
  expect(within(screen.getByRole("table", { name: "Agents" })).getByText("5200 ms")).toBeInTheDocument();
});

test("the console is for administrators only, and only they see its link", async () => {
  mockApi(STAFF);
  openConsole();
  expect(await screen.findByText("The admin console is for administrators only.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "Back to questions" }));
  expect(await screen.findByText("How can I help you today?")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /Admin console/ })).not.toBeInTheDocument();
});

test("an administrator reaches the console from the sidebar", async () => {
  mockApi(ADMIN);
  saveToken("tok-admin");
  render(<App />);
  fireEvent.click(await screen.findByRole("link", { name: /Admin console/ }));
  expect(await screen.findByRole("heading", { name: "Admin console" })).toBeInTheDocument();
  await waitFor(() => expect(window.location.pathname).toBe("/admin"));
});

test("single sign-on: Keycloak's redirect back signs the user in and cleans the address", async () => {
  // The pending sign-in this tab started (lib/oidc.ts writes it before leaving for Keycloak).
  window.sessionStorage.setItem("enterprise-fabric-ai.oidc-pending", JSON.stringify({
    issuer: "http://kc.test/realms/fabric", clientId: "fabric-ui", verifier: "v".repeat(43), state: "s-1",
    redirectUri: "http://localhost:3000/", returnTo: "/" }));
  const fetchMock = mockApi(STAFF, {});
  const api = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => String(url).startsWith("http://kc.test")
    ? Response.json({ access_token: "jwt-1", refresh_token: "r-1", expires_in: 300 }) : api(url, init));
  window.history.pushState(null, "", "/?code=c-1&state=s-1");
  render(<App />);
  expect(await screen.findByText("How can I help you today?")).toBeInTheDocument();
  expect(window.location.search).toBe("");
  const me = calls(fetchMock, "GET", "/auth/me")[0];
  expect(new Headers(me[1]!.headers).get("Authorization")).toBe("Bearer jwt-1");
  window.sessionStorage.clear();
});

test("in the full profile the sign-in screen offers only the firm's single sign-on", async () => {
  mockApi(STAFF, { "GET /auth/config": () => Response.json({ self_registration: false, mode: "oidc",
    oidc_issuer: "http://kc.test/realms/fabric", oidc_client_id: "fabric-ui" }) });
  render(<App />);
  expect(await screen.findByRole("button", { name: "Continue to firm sign-in" })).toBeInTheDocument();
  expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
});
