import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base_


class AlertSettings(Base_):
    """Where an org's alerts go - one row per org, created on first use.

    Every monitor (SNMP, ping, config change) produces alert rows and hands
    them to services/alerting.dispatch(), which delivers them through
    whichever of these channels are configured: email over SMTP, a
    Microsoft Teams incoming webhook, a Slack incoming webhook. The SMTP
    password is Fernet-encrypted at rest and never returned by the API."""

    __tablename__ = "alert_settings"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, unique=True)

    # Email
    recipients: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int] = mapped_column(Integer, default=587, nullable=False)
    smtp_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted_smtp_password: Mapped[str | None] = mapped_column(String(512), nullable=True)
    smtp_starttls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    smtp_ssl: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    smtp_from: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Chat webhooks (incoming-webhook URLs; treated as secrets - never logged)
    teams_webhook_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    slack_webhook_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Raise an alert when a collection finds a device's config changed
    # since its previous snapshot.
    alert_config_change: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
