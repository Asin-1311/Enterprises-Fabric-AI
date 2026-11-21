"""The index version (M16a, CLAUDE.md section 10).

New table index_state: one row holding a counter that the indexer raises after every
write to the indexes. The response cache puts it in its key, so an answer cached before a
document changed is never served after the change.

Revision ID: 0007_index_state
Revises: 0006_prompt_activation
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_index_state"
down_revision = "0006_prompt_activation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "index_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("index_state")
