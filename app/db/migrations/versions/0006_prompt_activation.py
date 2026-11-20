"""Prompt versions get a replay report and an activation time (M15c, CLAUDE.md 13.3).

New columns on prompt_versions:
- eval_report (JSON): the replay report; a new version can be activated only when it
  passed (the same gate as routing policies);
- activated_at: when the version last went live. A version that has served users before
  may be re-activated without a new replay: that is a rollback.

Before M15c every recorded prompt version became active when it was recorded (the code was
the source of the prompts), so existing rows get activated_at = created_at.

Revision ID: 0006_prompt_activation
Revises: 0005_feedback_created_at
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_prompt_activation"
down_revision = "0005_feedback_created_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("prompt_versions") as b:
        b.add_column(sa.Column("eval_report", sa.JSON(), nullable=True))
        b.add_column(sa.Column("activated_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE prompt_versions SET activated_at = created_at WHERE activated_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("prompt_versions") as b:
        b.drop_column("activated_at")
        b.drop_column("eval_report")
