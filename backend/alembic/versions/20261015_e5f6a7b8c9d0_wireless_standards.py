"""wireless_standards: one Wireless Audit standard per organization

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-15

Idempotent (see app.database.migrate_to_head).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.models.base

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("wireless_standards"):
        op.create_table(
            "wireless_standards",
            sa.Column("id", app.models.base.GUID(length=36), nullable=False),
            sa.Column("created_at", app.models.base.UTCDateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.Column("updated_at", app.models.base.UTCDateTime(timezone=True), nullable=True),
            sa.Column("org_id", app.models.base.GUID(length=36), nullable=False),
            sa.Column("settings", sa.Text(), nullable=False),
            sa.Column("updated_by_id", app.models.base.GUID(length=36), nullable=True),
            sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("org_id"),
        )


def downgrade() -> None:
    op.drop_table("wireless_standards")
