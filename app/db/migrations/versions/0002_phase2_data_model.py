"""Phase 2 data model (M11, CLAUDE.md section 9).

New tables: sources, routing_policies, prompt_versions, retrievals, feedback_signals.
New columns: users.is_admin; documents.source_id/uri/owner/classification/updated_at;
chunks.content_hash/chunking_profile/embedding_ref; queries.injection_hits;
traces.prompt_version_id/policy_version_id/created_at; answers.degraded/flagged_claims;
citations.span_start/span_end.

Columns that existing rows need (the two booleans) get a server default, so a database
that already holds data upgrades in place. Batch mode lets the same migration run on
SQLite (the drift test) as on PostgreSQL.

Revision ID: 0002_phase2_data_model
Revises: 0001_initial_schema
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_phase2_data_model"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("connector_config", sa.JSON(), nullable=False),
        sa.Column("owner", sa.String(), nullable=True),
        sa.Column("classification", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("last_event_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "routing_policies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("eval_report", sa.JSON(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("version"),
    )
    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("eval_score", sa.Float(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("agent", "version"),
    )
    op.create_table(
        "retrievals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("query_id", sa.Integer(), sa.ForeignKey("queries.id"), nullable=False),
        sa.Column("sub_query", sa.Text(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("scores", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_retrievals_query_id", "retrievals", ["query_id"])
    op.create_table(
        "feedback_signals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("answer_id", sa.Integer(), sa.ForeignKey("answers.id"), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_feedback_signals_answer_id", "feedback_signals", ["answer_id"])

    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("documents") as b:
        b.add_column(sa.Column("source_id", sa.String(), nullable=True))
        b.add_column(sa.Column("uri", sa.String(), nullable=True))
        b.add_column(sa.Column("owner", sa.String(), nullable=True))
        b.add_column(sa.Column("classification", sa.String(), nullable=True))
        b.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        b.create_foreign_key("fk_documents_source_id_sources", "sources", ["source_id"], ["id"])
        b.create_index("ix_documents_source_id", ["source_id"])
    with op.batch_alter_table("chunks") as b:
        b.add_column(sa.Column("content_hash", sa.String(), nullable=True))
        b.add_column(sa.Column("chunking_profile", sa.String(), nullable=True))
        b.add_column(sa.Column("embedding_ref", sa.String(), nullable=True))
    with op.batch_alter_table("queries") as b:
        b.add_column(sa.Column("injection_hits", sa.JSON(), nullable=True))
    with op.batch_alter_table("traces") as b:
        b.add_column(sa.Column("prompt_version_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("policy_version_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("created_at", sa.DateTime(), nullable=True))
        b.create_foreign_key("fk_traces_prompt_version_id", "prompt_versions",
                             ["prompt_version_id"], ["id"])
        b.create_foreign_key("fk_traces_policy_version_id", "routing_policies",
                             ["policy_version_id"], ["id"])
    with op.batch_alter_table("answers") as b:
        b.add_column(sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("flagged_claims", sa.JSON(), nullable=True))
    with op.batch_alter_table("citations") as b:
        b.add_column(sa.Column("span_start", sa.Integer(), nullable=True))
        b.add_column(sa.Column("span_end", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("citations") as b:
        b.drop_column("span_end")
        b.drop_column("span_start")
    with op.batch_alter_table("answers") as b:
        b.drop_column("flagged_claims")
        b.drop_column("degraded")
    with op.batch_alter_table("traces") as b:
        b.drop_constraint("fk_traces_policy_version_id", type_="foreignkey")
        b.drop_constraint("fk_traces_prompt_version_id", type_="foreignkey")
        b.drop_column("created_at")
        b.drop_column("policy_version_id")
        b.drop_column("prompt_version_id")
    with op.batch_alter_table("queries") as b:
        b.drop_column("injection_hits")
    with op.batch_alter_table("chunks") as b:
        b.drop_column("embedding_ref")
        b.drop_column("chunking_profile")
        b.drop_column("content_hash")
    with op.batch_alter_table("documents") as b:
        b.drop_index("ix_documents_source_id")
        b.drop_constraint("fk_documents_source_id_sources", type_="foreignkey")
        b.drop_column("updated_at")
        b.drop_column("classification")
        b.drop_column("owner")
        b.drop_column("uri")
        b.drop_column("source_id")
    with op.batch_alter_table("users") as b:
        b.drop_column("is_admin")
    for table in ("feedback_signals", "retrievals", "prompt_versions", "routing_policies",
                  "sources"):
        op.drop_table(table)
