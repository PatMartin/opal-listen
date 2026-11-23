"""drop regression_tests (regression feature removed from contract v0.2.0)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("regression_tests")


def downgrade() -> None:
    op.create_table(
        "regression_tests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False),
        sa.Column("phrase", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column("expected_intent", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("expected_slots", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_result", sa.Boolean(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
