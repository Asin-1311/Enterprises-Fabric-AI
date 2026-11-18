"""Request and response models for the API (/api/v1).

The signed-in user comes from the `Authorization: Bearer <token>` header (see
app/api/auth.py), never from the request body, so a request cannot claim to be someone
else. Unknown body fields are ignored, except on registration, where they are refused.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class SessionOut(BaseModel):
    """Returned by sign-in and registration. The token is shown once and never stored."""

    token: str
    expires_at: datetime  # UTC
    user: UserOut
