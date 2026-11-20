"""Classification policies (M16c, spec 7.10; CLAUDE.md section 11).

- New table classification_policies: an administrator's rule that documents of a source
  type, or whose path matches a glob, are at least confidential or restricted.
- New column documents.base_classification: the classification before any rule, so that
  removing a rule can restore it. Existing rows get their current classification.

Revision ID: 0008_classification_policies
Revises: 0007_index_state
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_classification_policies"
down_revision = "0007_index_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "classification_policies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("match", sa.String(), nullable=False),
        sa.Column("pattern", sa.String(), nullable=False),
        sa.Column("classification", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(), nullable=True),
    )
    with op.batch_alter_table("documents") as b:
        b.add_column(sa.Column("base_classification", sa.String(), nullable=True))
    op.execute("UPDATE documents SET base_classification = classification "
               "WHERE base_classification IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("documents") as b:
        b.drop_column("base_classification")
    op.drop_table("classification_policies")
