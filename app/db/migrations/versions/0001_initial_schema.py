"""Initial schema: every table as of Phase 1 + M9, with the append-only audit log.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-18

Written by hand from app/db/models.py. tests/test_migrations.py proves that the database
this migration builds matches the models exactly (no drift).
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

# The audit log must never be changed or emptied, even with raw SQL (CLAUDE.md section 12).
_SQLITE_GUARDS = (
    """CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
       BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
       BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END""",
)
_POSTGRES_GUARDS = (
    """CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
       BEGIN RAISE EXCEPTION 'audit_log is append-only'; END;
       $$ LANGUAGE plpgsql""",
    """CREATE TRIGGER audit_log_no_update_or_delete BEFORE UPDATE OR DELETE ON audit_log
       FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()""",
    """CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
       FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only()""",
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("password_hash", sa.String(), nullable=True),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_digest", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "password_resets",
        sa.Column("token_digest", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_password_resets_user_id", "password_resets", ["user_id"])
    op.create_table(
        "engagements",
        sa.Column("engagement_id", sa.String(), primary_key=True),
        sa.Column("client_name", sa.String(), nullable=False),
        sa.Column("industry", sa.String(), nullable=False),
        sa.Column("fy", sa.String(), nullable=False),
        sa.Column("partner_id", sa.String(), sa.ForeignKey("users.user_id"), nullable=False),
    )
    op.create_table(
        "engagement_members",
        sa.Column("engagement_id", sa.String(), sa.ForeignKey("engagements.engagement_id"),
                  primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.user_id"), primary_key=True),
    )
    op.create_table(
        "documents",
        sa.Column("doc_id", sa.String(), primary_key=True),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=True),
        sa.Column("effective_date", sa.String(), nullable=True),
        sa.Column("supersedes", sa.String(), nullable=True),
        sa.Column("access_level", sa.String(), nullable=False),
        sa.Column("engagement_id", sa.String(), sa.ForeignKey("engagements.engagement_id"),
                  nullable=True),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("indexed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("path"),
    )
    op.create_index("ix_documents_source_type", "documents", ["source_type"])
    op.create_table(
        "chunks",
        sa.Column("chunk_id", sa.String(), primary_key=True),
        sa.Column("doc_id", sa.String(), sa.ForeignKey("documents.doc_id"), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(), nullable=True),
        sa.Column("section_title", sa.String(), nullable=True),
        sa.Column("chapter", sa.Integer(), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("sa_refs", sa.String(), nullable=True),
    )
    op.create_index("ix_chunks_doc_id", "chunks", ["doc_id"])
    op.create_table(
        "queries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "traces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("query_id", sa.Integer(), sa.ForeignKey("queries.id"), nullable=False),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("input", sa.Text(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("effort", sa.String(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
    )
    op.create_index("ix_traces_query_id", "traces", ["query_id"])
    op.create_table(
        "answers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("query_id", sa.Integer(), sa.ForeignKey("queries.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
    )
    op.create_index("ix_answers_query_id", "answers", ["query_id"])
    op.create_table(
        "citations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("answer_id", sa.Integer(), sa.ForeignKey("answers.id"), nullable=False),
        sa.Column("chunk_id", sa.String(), nullable=False),
    )
    op.create_index("ix_citations_answer_id", "citations", ["answer_id"])
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("answer_id", sa.Integer(), sa.ForeignKey("answers.id"), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("rating", sa.String(), nullable=False),
        sa.Column("correction", sa.Text(), nullable=True),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    dialect = op.get_bind().dialect.name
    for ddl in _POSTGRES_GUARDS if dialect == "postgresql" else _SQLITE_GUARDS:
        op.execute(ddl)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
        op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_or_delete ON audit_log")
        op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
    for table in ("audit_log", "feedback", "citations", "answers", "traces", "queries",
                  "chunks", "documents", "engagement_members", "engagements",
                  "password_resets", "auth_sessions", "users"):
        op.drop_table(table)
