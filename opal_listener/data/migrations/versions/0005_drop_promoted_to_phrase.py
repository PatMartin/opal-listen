"""drop failed_utterances.promoted_to_phrase (promote op removed from contract v0.2.0)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("failed_utterances") as batch:
        batch.drop_column("promoted_to_phrase")


def downgrade() -> None:
    with op.batch_alter_table("failed_utterances") as batch:
        batch.add_column(sa.Column("promoted_to_phrase", sa.Boolean(), nullable=False, server_default=sa.false()))
