// Calls behind the admin console (M16c). Every route checks the admin flag on the server;
// the flag grants no extra document access, so traces and questions arrive without their
// content unless the administrator is also a partner.

import { callJson, post } from "../api";

export interface Source {
  id: string;
  type: string;
  connector_config: Record<string, unknown>;
  owner: string | null;
  classification: string;
  status: "active" | "paused" | string;
  last_event_at: string | null;
  created_at: string;
}

export interface SourceStatus {
  id: string;
  status: string;
  documents: number;
  chunks: number;
  last_event_at: string | null;
  freshness_lag_seconds: number | null;
  [key: string]: unknown;
}

export interface AdminQuery {
  query_id: number;
  user_id: string;
  asked_at: string;
  intent: string | null;
  status: string | null;
  question: string | null;
  question_hidden: boolean;
}

export interface TraceRow {
  agent: string;
  model: string | null;
  effort: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  latency_ms: number | null;
  prompt_version_id: number | null;
  policy_version_id: number | null;
  content_hidden: boolean;
  input: unknown;
  output: unknown;
}

interface Replay {
  replay?: { passed?: boolean; score?: number; baseline_score?: number } | string;
}

export interface PromptVersion {
  id: number;
  agent: string;
  version: number;
  template: string;
  eval_score: number | null;
  eval_report: Replay | null;
  created_by: string;
  created_at: string;
  activated_at: string | null;
  is_active: boolean;
}

export interface RoutingPolicy {
  id: number;
  version: number;
  weights: Record<string, unknown>;
  created_by: string;
  eval_report: Replay | null;
  activated_at: string | null;
  is_active: boolean;
}

export interface ClassificationPolicy {
  id: number;
  match: "source_type" | "path";
  pattern: string;
  classification: "confidential" | "restricted";
  active: boolean;
  created_by: string;
  created_at: string;
  deactivated_at: string | null;
}

export interface Performance {
  from: string;
  to: string;
  answers: number;
  answers_by_status: Record<string, number>;
  verification_steps: number;
  verification_failure_rate: number | null;
  degraded_rate: number | null;
  cache_hit_rate: number | null;
  estimated_cost_per_answer_usd: number | null;
  cost_basis: string;
  agents: { agent: string; steps: number; latency_p50_ms: number | null; latency_p95_ms: number | null;
    tokens_in: number; tokens_out: number }[];
}

/** "passed" | "failed" | "not required" | "none": what a version's replay report says. */
export function replayState(report: Replay | null): "passed" | "failed" | "not required" | "none" {
  const replay = report?.replay;
  if (typeof replay === "string") return "not required";
  if (!replay) return "none";
  return replay.passed ? "passed" : "failed";
}

const put = (body: unknown): RequestInit => ({ method: "PUT", body: JSON.stringify(body) });

export const listSources = () => callJson<Source[]>("/sources", {}, "Sources unavailable.");
export const sourceStatus = (id: string) =>
  callJson<SourceStatus>(`/sources/${encodeURIComponent(id)}/status`, {}, "Status unavailable.");
export const setSourcePaused = (id: string, paused: boolean) =>
  callJson<Source>(`/sources/${encodeURIComponent(id)}/${paused ? "pause" : "resume"}`, { method: "POST" },
    "The source could not be changed.");
export const registerSource = (body: { id: string; type: string; connector_config: Record<string, unknown> }) =>
  callJson<Source>("/sources", post(body), "The source could not be registered.");

export const recentQueries = () => callJson<AdminQuery[]>("/admin/queries?limit=50", {}, "Questions unavailable.");
export const getTrace = (queryId: number) =>
  callJson<TraceRow[]>(`/traces/${queryId}`, {}, "Trace unavailable.");

export const listPrompts = () => callJson<PromptVersion[]>("/admin/prompts", {}, "Prompts unavailable.");
export const activatePrompt = (id: number) =>
  callJson<{ action: string; version: PromptVersion }>(`/admin/prompts/${id}/activate`, { method: "POST" },
    "The prompt version could not be activated.");

export const listRoutingPolicies = () =>
  callJson<RoutingPolicy[]>("/admin/routing-policies", {}, "Routing policies unavailable.");
export const activateRoutingPolicy = (version: number) =>
  callJson<RoutingPolicy>("/routing-policy", put({ version }), "The routing policy could not be activated.");

export const listClassificationPolicies = () =>
  callJson<ClassificationPolicy[]>("/admin/classification-policies", {}, "Classification policies unavailable.");
export const createClassificationPolicy = (body: Pick<ClassificationPolicy, "match" | "pattern" | "classification">) =>
  callJson<{ policy: ClassificationPolicy; reclassified: string[] }>("/admin/classification-policies", post(body),
    "The rule could not be saved.");
export const deactivateClassificationPolicy = (id: number) =>
  callJson<{ policy: ClassificationPolicy; reclassified: string[] }>(
    `/admin/classification-policies/${id}/deactivate`, { method: "POST" }, "The rule could not be switched off.");

export const agentPerformance = () =>
  callJson<Performance>("/admin/agents/performance", {}, "Performance figures unavailable.");
