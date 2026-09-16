"""credentials: optional TOTP seed for unattended passcode MFA

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-16

Idempotent (see app.database.migrate_to_head).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.models.base  # noqa: F401

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("credentials")}
    if "encrypted_totp_secret" not in columns:
        with op.batch_alter_table("credentials") as batch:
            batch.add_column(sa.Column("encrypted_totp_secret", sa.String(length=512), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("credentials") as batch:
        batch.drop_column("encrypted_totp_secret")
