"""Request and response models for the API (/api/v1).

The signed-in user comes from the `Authorization: Bearer <token>` header (see
app/api/auth.py), never from the request body, so a request cannot claim to be someone
else. Unknown body fields are ignored, except on registration, where they are refused.
"""

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalise_email(value: str) -> str:
    """Trim and lower-case; reject anything that is not shaped like an email address."""
    value = value.strip().lower()
    if not _EMAIL.match(value) or len(value) > 254:
        raise ValueError("Enter a valid email address.")
    return value


class QueryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class FeedbackRequest(BaseModel):
    answer_id: int
    rating: Literal["helpful", "not_helpful"]
    correction: str | None = Field(default=None, max_length=4000)


# ------------------------------------------------------------------------------ sign-in

class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)

    check_email = field_validator("email")(normalise_email)


class RegisterRequest(BaseModel):
    """New accounts are always staff with no engagements. Extra fields (e.g. "role") are
    refused rather than ignored, so the rule is visible in the API's behaviour."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)

    check_email = field_validator("email")(normalise_email)

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("Enter your full name.")
        return value


class ForgotPasswordRequest(BaseModel):
    email: str = Field(max_length=254)

    check_email = field_validator("email")(normalise_email)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(max_length=128)


class CitationOut(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    section: str | None
    section_title: str | None
    page_start: int | None
    page_end: int | None
    superseded: bool
    preview: str
    # Character range in the chunk's text of the sentences supporting the answer (M11);
    # None when they could not be located.
    span_start: int | None = None
    span_end: int | None = None
    # M16b: that passage itself (spaces normalised), which the preview contains, so the UI
    # can highlight it; None when the span is unknown.
    cited_text: str | None = None


class FinalPayload(BaseModel):
    """The last SSE event of POST /query."""

    query_id: int
    answer_id: int
    status: str
    text: str
    confidence: float | None
    claims_supported: int
    claims_factual: int
    removed_claims: int
    superseded_involved: bool
    injection_flagged_chunks: int
    citations: list[CitationOut]
    seconds_to_answer: float | None  # None when a past answer is reopened from history
    # M14b: claims the LLM verifier accepted but the NLI model found contradicted by their
    # cited text; they stay in the answer, marked "check this point".
    flagged_claims: list[str] = []
    # M15a: answered under load WITHOUT verification (low-risk questions only); the text
    # starts with the "Reduced checking" notice and there is no confidence score.
    degraded: bool = False


class HistoryItem(BaseModel):
    """One past question of the signed-in user (sidebar History)."""

    query_id: int
    question: str  # as stored (masked)
    status: str
    asked_at: datetime  # UTC


class TraceRow(BaseModel):
    agent: str
    model: str | None
    effort: str | None
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: float | None
    input: object
    output: object
    # M11: which prompt and routing-policy versions the step used.
    prompt_version_id: int | None = None
    policy_version_id: int | None = None
    # True when input/output are withheld: an administrator who is not a partner sees how
    # each step ran (model, tokens, timing, versions) but not the document text or the
    # draft answer, because the admin flag gives no document access (CLAUDE.md section 5).
    content_hidden: bool = False


class QueryDetail(BaseModel):
    query_id: int
    user_id: str
    question: str  # as stored (masked)
    intent: str | None
    status: str | None
    answer: str | None
    confidence: float | None
    citations: list[CitationOut]
    trace: list[TraceRow]


class UserOut(BaseModel):
    user_id: str
    name: str
    role: str
    email: str | None = None
    engagements: list[str]  # client names; partners see all
    is_admin: bool = False  # admin console and source management; no extra document access


class SessionOut(BaseModel):
    """Returned by sign-in and registration. The token is shown once and never stored."""

    token: str
    expires_at: datetime  # UTC
    user: UserOut


# --------------------------------------------------------------- typed responses (M11)
# Every endpoint declares its response shape, so the OpenAPI schema documents it and the
# contract tests can check real responses against it (CLAUDE.md section 13.3).

class HealthOut(BaseModel):
    status: Literal["ok"]


class AuthConfigOut(BaseModel):
    self_registration: bool
    mode: Literal["session", "oidc"] = "session"  # M13b: who signs users in
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None


class SignedOutOut(BaseModel):
    signed_out: bool


class MessageOut(BaseModel):
    message: str


class PasswordResetOut(BaseModel):
    reset: bool


class FeedbackOut(BaseModel):
    feedback_id: int
    stored: bool


SignalKind = Literal["copy", "citation_click", "follow_up", "rephrase"]


class SignalRequest(BaseModel):
    """An implicit signal from the UI about an answer (stored; used from M15)."""

    model_config = ConfigDict(extra="forbid")
    answer_id: int
    kind: SignalKind


class SignalOut(BaseModel):
    signal_id: int
    stored: bool


class SourceTypeStatus(BaseModel):
    source_type: str
    documents: int
    chunks: int
    last_indexed_at: str | None


class QualityRun(BaseModel):
    run: str
    type: str
    started_at: str
    metrics: dict[str, Any]  # metrics.json exactly as the evaluation script wrote it


class QualityMetricsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    runs: list[QualityRun]


# --------------------------------------------------------------- sources (M11)

ConnectorType = Literal["filesystem", "git", "s3", "webhook", "dms", "confluence", "jira",
                        "sharepoint"]
Classification = Literal["public_internal", "confidential", "restricted"]


class SourceCreate(BaseModel):
    """Register a source. Connector settings only: secrets are referenced as "env:NAME"."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    type: ConnectorType
    connector_config: dict[str, Any]
    owner: str | None = None  # a user id
    classification: Classification = "public_internal"


class SourceOut(BaseModel):
    id: str
    type: str
    connector_config: dict[str, Any]
    owner: str | None
    classification: str
    status: str
    last_event_at: datetime | None
    created_at: datetime


class SourceStatusOut(BaseModel):
    id: str
    type: str
    status: str
    classification: str
    documents: int
    chunks: int
    last_event_at: datetime | None  # last document change seen from this source (UTC)
    last_indexed_at: datetime | None
    freshness_lag_seconds: float | None  # time since the last change, None if never


# --------------------------------------------------------------- re-index (M11)

class ReindexRequest(BaseModel):
    """Exactly one of: a document, a source, or everything."""

    model_config = ConfigDict(extra="forbid")
    doc_id: str | None = None
    source_id: str | None = None
    all: bool = False

    @model_validator(mode="after")
    def exactly_one(self) -> "ReindexRequest":
        chosen = [self.doc_id is not None, self.source_id is not None, self.all]
        if sum(chosen) != 1:
            raise ValueError('Give exactly one of "doc_id", "source_id" or "all": true.')
        return self


class ReindexOut(BaseModel):
    scope: Literal["document", "source", "all"]
    target: str | None
    indexed: list[str]
    metadata_updated: list[str]
    unchanged: int
    removed: list[str]


# --------------------------------------------------------------- routing policy (M11)

class RoutingPolicyActivate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class RoutingPolicyOut(BaseModel):
    id: int
    version: int
    weights: dict[str, Any]
    created_by: str
    eval_report: dict[str, Any] | None
    activated_at: datetime | None
    is_active: bool


# --------------------------------------------------------------- prompt registry (M15c)

class PromptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str = Field(min_length=1, max_length=64)
    template: str = Field(min_length=1, max_length=20_000)


class PromptVersionOut(BaseModel):
    id: int
    agent: str
    version: int
    template: str
    eval_score: float | None
    eval_report: dict[str, Any] | None
    created_by: str
    created_at: datetime
    activated_at: datetime | None
    is_active: bool


class PromptActivationOut(BaseModel):
    action: Literal["unchanged", "prompt_activated", "prompt_rolled_back"]
    version: PromptVersionOut


# --------------------------------------------------------------- admin console (M16c)

class ClassificationPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match: Literal["source_type", "path"]
    pattern: str = Field(min_length=1, max_length=300)
    classification: Literal["confidential", "restricted"]


class ClassificationPolicyOut(BaseModel):
    id: int
    match: str
    pattern: str
    classification: str
    active: bool
    created_by: str
    created_at: datetime
    deactivated_at: datetime | None


class ClassificationChangeOut(BaseModel):
    policy: ClassificationPolicyOut
    reclassified: list[str]  # documents whose classification changed now


class AdminQueryItem(BaseModel):
    """A recent question, for finding its trace. The question text is shown to partners
    only: an administrator who is not a partner gets no extra document access."""

    query_id: int
    user_id: str
    asked_at: datetime
    intent: str | None
    status: str | None
    question: str | None
    question_hidden: bool


class AgentPerformanceItem(BaseModel):
    agent: str
    steps: int
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    tokens_in: int
    tokens_out: int


class AgentPerformanceOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from")
    to: str
    source: Literal["prometheus", "traces"] = "traces"  # M17a: where the figures came from
    answers: int
    answers_by_status: dict[str, int]
    verification_steps: int
    verification_failure_rate: float | None
    degraded_rate: float | None
    cache_hit_rate: float | None
    estimated_cost_per_answer_usd: float | None
    cost_basis: str
    agents: list[AgentPerformanceItem]
