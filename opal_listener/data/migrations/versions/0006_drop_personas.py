"""drop personas table

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-15

Personas were removed from the opal-listen contract in 0.6.0 (persona identity lives
with opal-say / opal-think). Existing databases drop the now-unused table; fresh
databases never create it (0002 is retained for history, so 0006 undoes it).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("personas")


def downgrade() -> None:
    import sqlalchemy as sa
    import sqlmodel

    op.create_table(
        "personas",
        sa.Column("persona_id", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(length=1000), nullable=True),
        sa.Column("voice_id", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("persona_id"),
    )
