"""dns check subnet sweeps: the subnet a DNS check job swept

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-30

Idempotent (see app.database.migrate_to_head).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("dns_check_jobs")}
    if "subnet" not in columns:
        with op.batch_alter_table("dns_check_jobs") as batch:
            batch.add_column(sa.Column("subnet", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dns_check_jobs") as batch:
        batch.drop_column("subnet")
