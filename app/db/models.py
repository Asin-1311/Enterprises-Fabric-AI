"""SQLAlchemy ORM models for the SQLite metadata store (CLAUDE.md section 8).

Tables: users, engagements, engagement_members, documents, chunks, queries,
traces, answers, citations, feedback, audit_log; plus auth_sessions and
password_resets for sign-in (added 2026-11-14 at the author's request).

`audit_log` is append-only: an ORM event hook raises if any code tries to
update or delete an existing row, and no update/delete helpers exist for it.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    event,
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


class Query(Base):
    """A question asked by a user."""

    __tablename__ = "queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    text: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


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


class Answer(Base):
    """The final answer to a query. status e.g. 'answered' | 'abstained' | 'denied'."""

    __tablename__ = "answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_id: Mapped[int] = mapped_column(ForeignKey("queries.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String)


class Citation(Base):
    """A chunk cited by an answer."""

    __tablename__ = "citations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"), index=True)
    chunk_id: Mapped[str] = mapped_column(String)


class Feedback(Base):
    """User feedback on an answer (stored only; no learning loop)."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    rating: Mapped[str] = mapped_column(String)  # 'helpful' | 'not_helpful'
    correction: Mapped[str | None] = mapped_column(Text, nullable=True)


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
