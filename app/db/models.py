"""SQLAlchemy ORM models for the SQLite metadata store (CLAUDE.md section 8).

Tables: users, engagements, engagement_members, documents, chunks, queries,
traces, answers, citations, feedback, audit_log; plus auth_sessions and
password_resets for sign-in (added 2026-11-14 at the author's request); plus, for
Phase 2 (M11, CLAUDE.md section 9): sources, retrievals, routing_policies,
prompt_versions and feedback_signals, and new columns on several tables.

New columns that existing rows need get a server-side default, so an older database
upgrades in place (SQLite through init_db, PostgreSQL through Alembic migration 0002).

`audit_log` is append-only: an ORM event hook raises if any code tries to
update or delete an existing row, and no update/delete helpers exist for it.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """Current time in UTC (used as a column default)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class User(Base):
    """A user. role is 'staff' | 'manager' | 'partner'.

    email and password_hash were added for sign-in (2026-11-14). The password itself is
    never stored: only a salted scrypt hash (app/security/passwords.py)."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    synthetic: Mapped[bool] = mapped_column(default=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)  # lower case
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    # Admin console and source management (M11). Grants NO extra document access.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class AuthSession(Base):
    """A signed-in session. Only the SHA-256 digest of the token is stored, so a copy of
    the database cannot be used to sign in."""

    __tablename__ = "auth_sessions"

    token_digest: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)  # naive UTC
    expires_at: Mapped[datetime] = mapped_column(DateTime)  # naive UTC


class PasswordReset(Base):
    """A one-time password-reset link (digest only), valid for a few minutes."""

    __tablename__ = "password_resets"

    token_digest: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)  # naive UTC
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Engagement(Base):
    """A synthetic client engagement (one client, one financial year)."""

    __tablename__ = "engagements"

    engagement_id: Mapped[str] = mapped_column(String, primary_key=True)
    client_name: Mapped[str] = mapped_column(String)
    industry: Mapped[str] = mapped_column(String)
    fy: Mapped[str] = mapped_column(String)
    partner_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))


class EngagementMember(Base):
    """Link table: which users are on which engagement team."""

    __tablename__ = "engagement_members"

    engagement_id: Mapped[str] = mapped_column(
        ForeignKey("engagements.engagement_id"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), primary_key=True)


class Document(Base):
    """One source document, mirroring a row of data/manifest.csv."""

    __tablename__ = "documents"

    doc_id: Mapped[str] = mapped_column(String, primary_key=True)
    path: Mapped[str] = mapped_column(String, unique=True)
    source_type: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String)
    version: Mapped[str | None] = mapped_column(String, nullable=True)
    effective_date: Mapped[str | None] = mapped_column(String, nullable=True)  # YYYY-MM-DD
    supersedes: Mapped[str | None] = mapped_column(String, nullable=True)  # doc_id
    access_level: Mapped[str] = mapped_column(String)
    engagement_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagements.engagement_id"), nullable=True
    )
    synthetic: Mapped[bool] = mapped_column(default=True)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # M11: where the document came from, and enrichment fields (filled by M12).
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"), nullable=True,
                                                  index=True)
    uri: Mapped[str | None] = mapped_column(String, nullable=True)
    owner: Mapped[str | None] = mapped_column(String, nullable=True)
    classification: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Chunk(Base):
    """A retrievable piece of a document. parent_id links a child to its section."""

    __tablename__ = "chunks"

    chunk_id: Mapped[str] = mapped_column(String, primary_key=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("documents.doc_id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String, nullable=True)
    # section_title and chapter are not in the CLAUDE.md section 8 table, but section 6
    # asks for them as chunk metadata (the UI shows them in citations).
    section_title: Mapped[str | None] = mapped_column(String, nullable=True)
    chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sa_refs: Mapped[str | None] = mapped_column(String, nullable=True)  # comma-separated
    # M11/M12b: chunk-level incremental updates and chunking profiles.
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)  # SHA-256 of text
    chunking_profile: Mapped[str | None] = mapped_column(String, nullable=True)  # "faq@1:400/50"
    embedding_ref: Mapped[str | None] = mapped_column(String, nullable=True)  # embedding model
    # M12b: the Phase 1 position id ("STD-CH06:0004") this chunk had before stable ids, so
    # citations in answers written before M12b still resolve. None for newer chunks.
    legacy_chunk_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)


class Query(Base):
    """A question asked by a user."""

    __tablename__ = "queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    text: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    # M11: instruction-like patterns found in the question (app/security/injection.py).
    injection_hits: Mapped[list | None] = mapped_column(JSON, nullable=True)


class Trace(Base):
    """One row per agent step. input/output are masked before insert."""

    __tablename__ = "traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_id: Mapped[int] = mapped_column(ForeignKey("queries.id"), index=True)
    agent: Mapped[str] = mapped_column(String)
    input: Mapped[str | None] = mapped_column(Text, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    effort: Mapped[str | None] = mapped_column(String, nullable=True)  # CLAUDE.md §3 call rules
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    # M11: which prompt and routing policy produced this step, and when.
    prompt_version_id: Mapped[int | None] = mapped_column(ForeignKey("prompt_versions.id"),
                                                          nullable=True)
    policy_version_id: Mapped[int | None] = mapped_column(ForeignKey("routing_policies.id"),
                                                          nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=utc_now)


class Answer(Base):
    """The final answer to a query. status e.g. 'answered' | 'abstained' | 'denied'."""

    __tablename__ = "answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_id: Mapped[int] = mapped_column(ForeignKey("queries.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String)
    # M11 columns, used from M14/M15: verification reduced under load; claims that the NLI
    # model contradicted but the LLM verifier accepted ("check this point").
    degraded: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    flagged_claims: Mapped[list | None] = mapped_column(JSON, nullable=True)


class Citation(Base):
    """A chunk cited by an answer, with the character span of the supporting text in the
    chunk's text (M11; None when it could not be located)."""

    __tablename__ = "citations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"), index=True)
    chunk_id: Mapped[str] = mapped_column(String)
    span_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_end: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Feedback(Base):
    """User feedback on an answer. Since M15b it feeds the routing bandit's rewards
    (app/adaptive/routing_policy.py): "not helpful" within 24 hours makes an answer a failure."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    rating: Mapped[str] = mapped_column(String)  # 'helpful' | 'not_helpful'
    correction: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=utc_now)  # M15b


class Source(Base):
    """A registered place documents come from (folder, Git repo, bucket, ...; M11).

    connector_config holds settings only: secrets are referenced (e.g. "env:NAME"), never
    stored (app/api/admin.py refuses them)."""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "fs-firm_policy"
    type: Mapped[str] = mapped_column(String)  # filesystem | git | s3 | webhook | ...
    connector_config: Mapped[dict] = mapped_column(JSON)
    owner: Mapped[str | None] = mapped_column(String, nullable=True)
    classification: Mapped[str] = mapped_column(String)  # public_internal | confidential | restricted
    status: Mapped[str] = mapped_column(String)  # active | paused
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    # M12c: what a polling connector saw last time (item -> version token), when it last
    # polled, and its last error. Written by app/connectors/runner.py only.
    sync_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Retrieval(Base):
    """One search inside answering a question: per sub-query and method (dense, keyword)
    the raw scores and ranks, and for the fused list every multiplier and weight applied
    (M11; audit finding R-8-record)."""

    __tablename__ = "retrievals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_id: Mapped[int] = mapped_column(ForeignKey("queries.id"), index=True)
    sub_query: Mapped[str] = mapped_column(Text)  # masked
    source_id: Mapped[str | None] = mapped_column(String, nullable=True)
    method: Mapped[str] = mapped_column(String)  # dense | keyword | fused
    scores: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class RoutingPolicy(Base):
    """A version of the source-routing policy. Exactly one is active. A new version can be
    activated only with a passing replay report (CLAUDE.md section 13.3)."""

    __tablename__ = "routing_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    weights: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String)
    eval_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class PromptVersion(Base):
    """A version of one agent's prompt. Exactly one per agent is active."""

    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("agent", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent: Mapped[str] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer)
    template: Mapped[str] = mapped_column(Text)
    eval_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    eval_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # replay (M15c)
    created_by: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class FeedbackSignal(Base):
    """An implicit signal from the UI (copy, citation click, follow-up, rephrase)."""

    __tablename__ = "feedback_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    kind: Mapped[str] = mapped_column(String)  # copy | citation_click | follow_up | rephrase
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class IndexState(Base):
    """One row: the index version, raised after every write to the indexes (M16a). The
    response cache puts it in its key, so any index change makes older answers unusable."""

    __tablename__ = "index_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    """Append-only record of administrative actions (reindex, manifest changes)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    target: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class AuditLogImmutableError(RuntimeError):
    """Raised when code attempts to change or remove an audit_log row."""


@event.listens_for(AuditLog, "before_update")
def _block_audit_update(mapper, connection, target) -> None:  # noqa: ANN001
    raise AuditLogImmutableError("audit_log is append-only: updates are not allowed")


@event.listens_for(AuditLog, "before_delete")
def _block_audit_delete(mapper, connection, target) -> None:  # noqa: ANN001
    raise AuditLogImmutableError("audit_log is append-only: deletes are not allowed")
