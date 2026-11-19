"""Connector state (M12c, CLAUDE.md section 6).

New column: sources.sync_state (JSON), what a polling connector saw on its last poll
(each item's version token and document id), when it polled and its last error. The
runner compares the next listing with it to find what changed.

Revision ID: 0004_source_sync_state
Revises: 0003_legacy_chunk_id
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_source_sync_state"
down_revision = "0003_legacy_chunk_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sources") as b:
        b.add_column(sa.Column("sync_state", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sources") as b:
        b.drop_column("sync_state")
