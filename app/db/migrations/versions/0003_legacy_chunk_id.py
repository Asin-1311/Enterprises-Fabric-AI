"""Stable chunk ids (M12b, CLAUDE.md section 6).

New column: chunks.legacy_chunk_id, the Phase 1 position id ("STD-CH06:0004") a chunk had
before ids became `<doc_id>:<section>:<window>`. Answers written before M12b cite the old
ids; the API resolves them through this column. Indexed, because it is looked up.

Revision ID: 0003_legacy_chunk_id
Revises: 0002_phase2_data_model
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_legacy_chunk_id"
down_revision = "0002_phase2_data_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chunks") as b:
        b.add_column(sa.Column("legacy_chunk_id", sa.String(), nullable=True))
        b.create_index("ix_chunks_legacy_chunk_id", ["legacy_chunk_id"])


def downgrade() -> None:
    with op.batch_alter_table("chunks") as b:
        b.drop_index("ix_chunks_legacy_chunk_id")
        b.drop_column("legacy_chunk_id")
