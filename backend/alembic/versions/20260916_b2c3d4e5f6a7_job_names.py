"""collection jobs get a human-readable name

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-16

Idempotent (see app.database.migrate_to_head): a database adopted from
create_all may already have the column.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.models.base  # noqa: F401 - custom types referenced by autogenerate

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("collection_jobs")}
    if "name" not in columns:
        with op.batch_alter_table("collection_jobs") as batch:
            batch.add_column(sa.Column("name", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("collection_jobs") as batch:
        batch.drop_column("name")
