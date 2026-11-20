"""When feedback was given (M15b, CLAUDE.md section 8).

New column: feedback.created_at. The routing bandit's reward needs "no 'not helpful'
feedback within 24 hours of the answer", so the time of the feedback must be known.
Rows from before this migration have none and are treated as given at answer time.

Revision ID: 0005_feedback_created_at
Revises: 0004_source_sync_state
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_feedback_created_at"
down_revision = "0004_source_sync_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("feedback") as b:
        b.add_column(sa.Column("created_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("feedback") as b:
        b.drop_column("created_at")
