"""profiles: shared + foreign-module compartments (x-opal-profiles v3)

The opal-listen compartment keeps its dedicated columns; `shared` holds the
closed shared object and `modules` every other module's compartment verbatim.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.add_column(sa.Column("shared", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("modules", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.drop_column("modules")
        batch.drop_column("shared")
