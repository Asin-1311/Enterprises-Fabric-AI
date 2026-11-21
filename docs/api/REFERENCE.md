# API reference: Enterprise Fabric AI API 0.7.0

Generated from `docs/api/openapi.json` by `python -m scripts.make_docs`; do not edit.
The contract tests check every endpoint against that schema. All endpoints need a bearer
token except `/health`, `/metrics` and the lite sign-in routes.

| Method | Path | Purpose | Request body | Responses |
|---|---|---|---|---|
| GET | `/api/v1/admin/agents/performance` | Agents Performance |  | 200, 422 |
| GET | `/api/v1/admin/classification-policies` | List Classification Policies |  | 200 |
| POST | `/api/v1/admin/classification-policies` | Create Classification Policy | ClassificationPolicyCreate | 201, 422 |
| POST | `/api/v1/admin/classification-policies/{policy_id}/deactivate` | Deactivate Classification Policy |  | 200, 422 |
| GET | `/api/v1/admin/prompts` | List Prompts |  | 200, 422 |
| POST | `/api/v1/admin/prompts` | Create Prompt | PromptCreate | 201, 422 |
| POST | `/api/v1/admin/prompts/{version_id}/activate` | Activate Prompt |  | 200, 422 |
| GET | `/api/v1/admin/queries` | Recent Queries |  | 200, 422 |
| GET | `/api/v1/admin/routing-policies` | List Routing Policies |  | 200 |
| GET | `/api/v1/auth/config` | Auth Config |  | 200 |
| POST | `/api/v1/auth/forgot-password` | Forgot Password | ForgotPasswordRequest | 200, 422 |
| POST | `/api/v1/auth/login` | Login | LoginRequest | 200, 422 |
| POST | `/api/v1/auth/logout` | Logout |  | 200 |
| GET | `/api/v1/auth/me` | Me |  | 200 |
| POST | `/api/v1/auth/register` | Register | RegisterRequest | 201, 422 |
| POST | `/api/v1/auth/reset-password` | Reset Password | ResetPasswordRequest | 200, 422 |
| POST | `/api/v1/documents/reindex` | Reindex | ReindexRequest | 200, 422 |
| POST | `/api/v1/feedback` | Feedback | FeedbackRequest | 200, 422 |
| POST | `/api/v1/feedback/signals` | Feedback Signal | SignalRequest | 201, 422 |
| GET | `/api/v1/health` | Health |  | 200 |
| GET | `/api/v1/history` | History |  | 200, 422 |
| GET | `/api/v1/history/{query_id}` | History Answer |  | 200, 422 |
| GET | `/api/v1/metrics/quality` | Metrics Quality |  | 200, 422 |
| POST | `/api/v1/query` | Query | QueryRequest | 200, 422 |
| GET | `/api/v1/query/{query_id}` | Get Query |  | 200, 422 |
| PUT | `/api/v1/routing-policy` | Activate Routing Policy | RoutingPolicyActivate | 200, 422 |
| GET | `/api/v1/sources` | List Sources |  | 200 |
| POST | `/api/v1/sources` | Register Source | SourceCreate | 201, 422 |
| GET | `/api/v1/sources/status` | Sources Status |  | 200 |
| POST | `/api/v1/sources/{source_id}/pause` | Pause Source |  | 200, 422 |
| POST | `/api/v1/sources/{source_id}/resume` | Resume Source |  | 200, 422 |
| GET | `/api/v1/sources/{source_id}/status` | Source Status |  | 200, 422 |
| GET | `/api/v1/traces/{query_id}` | Get Traces |  | 200, 422 |
| GET | `/api/v1/users` | Users |  | 200 |
| POST | `/api/v1/webhooks/documents` | Receive Document |  | 202, 422 |
