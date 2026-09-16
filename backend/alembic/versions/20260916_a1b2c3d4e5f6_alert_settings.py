"""alert settings: one place for delivery channels

Moves email delivery (recipients + SMTP) off snmp_monitor_configs into a
new alert_settings table shared by every monitor, adds Teams/Slack webhook
URLs and the config-change toggle there, and gives snmp_alerts the columns
that record which channels delivered an alert plus the job that raised a
config-change alert.

Written to tolerate a database adopted from create_all (see
app.database.migrate_to_head): every step checks what already exists.

Revision ID: a1b2c3d4e5f6
Revises: 7be3e846af07
Create Date: 2026-09-16
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.models.base

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "7be3e846af07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SMTP_COLUMNS = ("recipients", "smtp_host", "smtp_port", "smtp_username", "encrypted_smtp_password", "smtp_starttls", "smtp_ssl", "smtp_from")


def _columns(inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)} if inspector.has_table(table) else set()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("alert_settings"):
        op.create_table(
            "alert_settings",
            sa.Column("id", app.models.base.GUID(length=36), nullable=False),
            sa.Column("created_at", app.models.base.UTCDateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.Column("updated_at", app.models.base.UTCDateTime(timezone=True), nullable=True),
            sa.Column("org_id", app.models.base.GUID(length=36), nullable=False),
            sa.Column("recipients", sa.Text(), nullable=True),
            sa.Column("smtp_host", sa.String(length=255), nullable=True),
            sa.Column("smtp_port", sa.Integer(), nullable=False),
            sa.Column("smtp_username", sa.String(length=255), nullable=True),
            sa.Column("encrypted_smtp_password", sa.String(length=512), nullable=True),
            sa.Column("smtp_starttls", sa.Boolean(), nullable=False),
            sa.Column("smtp_ssl", sa.Boolean(), nullable=False),
            sa.Column("smtp_from", sa.String(length=255), nullable=True),
            sa.Column("teams_webhook_url", sa.String(length=1024), nullable=True),
            sa.Column("slack_webhook_url", sa.String(length=1024), nullable=True),
            sa.Column("alert_config_change", sa.Boolean(), nullable=False),
            sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("org_id"),
        )

    # Carry existing email settings across so nobody's alerts go quiet.
    monitor_columns = _columns(inspector, "snmp_monitor_configs")
    if set(SMTP_COLUMNS) <= monitor_columns:
        rows = bind.execute(
            sa.text(
                "SELECT id, org_id, recipients, smtp_host, smtp_port, smtp_username, encrypted_smtp_password, "
                "smtp_starttls, smtp_ssl, smtp_from FROM snmp_monitor_configs WHERE smtp_host IS NOT NULL OR recipients IS NOT NULL"
            )
        ).mappings().all()
        existing_orgs = {r[0] for r in bind.execute(sa.text("SELECT org_id FROM alert_settings")).all()}
        for row in rows:
            if row["org_id"] in existing_orgs:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO alert_settings (id, created_at, org_id, recipients, smtp_host, smtp_port, smtp_username, "
                    "encrypted_smtp_password, smtp_starttls, smtp_ssl, smtp_from, alert_config_change) "
                    "VALUES (:id, CURRENT_TIMESTAMP, :org_id, :recipients, :smtp_host, :smtp_port, :smtp_username, "
                    ":encrypted_smtp_password, :smtp_starttls, :smtp_ssl, :smtp_from, :acc)"
                ),
                {
                    "id": row["id"],  # reuse the config row's id: unique and stable
                    "org_id": row["org_id"],
                    "recipients": row["recipients"],
                    "smtp_host": row["smtp_host"],
                    "smtp_port": row["smtp_port"] or 587,
                    "smtp_username": row["smtp_username"],
                    "encrypted_smtp_password": row["encrypted_smtp_password"],
                    "smtp_starttls": bool(row["smtp_starttls"]),
                    "smtp_ssl": bool(row["smtp_ssl"]),
                    "smtp_from": row["smtp_from"],
                    "acc": True,
                },
            )
        with op.batch_alter_table("snmp_monitor_configs") as batch:
            for column in SMTP_COLUMNS:
                batch.drop_column(column)

    alert_columns = _columns(inspector, "snmp_alerts")
    with op.batch_alter_table("snmp_alerts") as batch:
        if "job_id" not in alert_columns:
            batch.add_column(sa.Column("job_id", app.models.base.GUID(length=36), nullable=True))
        if "notified_via" not in alert_columns:
            batch.add_column(sa.Column("notified_via", sa.String(length=255), nullable=True))
        if "webhook_error" not in alert_columns:
            batch.add_column(sa.Column("webhook_error", sa.Text(), nullable=True))

    if bind.dialect.name == "postgresql":
        # Native enum: new members must be added explicitly (SQLite stores plain text).
        for value in ("PING_DOWN", "PING_UP", "CONFIG_CHANGED"):
            op.execute(sa.text(f"ALTER TYPE snmpalertkind ADD VALUE IF NOT EXISTS '{value}'"))


def downgrade() -> None:
    with op.batch_alter_table("snmp_alerts") as batch:
        batch.drop_column("webhook_error")
        batch.drop_column("notified_via")
        batch.drop_column("job_id")
    with op.batch_alter_table("snmp_monitor_configs") as batch:
        batch.add_column(sa.Column("recipients", sa.Text(), nullable=True))
        batch.add_column(sa.Column("smtp_host", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="587"))
        batch.add_column(sa.Column("smtp_username", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("encrypted_smtp_password", sa.String(length=512), nullable=True))
        batch.add_column(sa.Column("smtp_starttls", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("smtp_ssl", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("smtp_from", sa.String(length=255), nullable=True))
    op.drop_table("alert_settings")
