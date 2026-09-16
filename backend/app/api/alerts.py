"""Alerts page: delivery channels shared by every monitor, and the alert
history. Settings are admin-only; the history is visible to everyone in
the org."""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.core.encryption import encrypt_secret
from app.database import get_db
from app.models.alerting import AlertSettings
from app.models.organization import Organization
from app.models.snmp_monitor import SnmpAlert
from app.models.user import User
from app.schemas.alerting import AlertSettingsOut, AlertSettingsUpdate, AlertTestResult
from app.schemas.snmp import SnmpJobClearResult
from app.schemas.snmp_monitor import SnmpAlertOut
from app.services import alerting
from app.services.snmp_monitor import KIND_LABELS, parse_recipients

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


async def _get_or_create(db: AsyncSession, org_id) -> AlertSettings:
    settings = await db.scalar(select(AlertSettings).where(AlertSettings.org_id == org_id))
    if settings is None:
        settings = AlertSettings(org_id=org_id)
        db.add(settings)
        await db.flush()
    return settings


def _host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlparse(url).netloc or None
    except ValueError:
        return None


def _out(s: AlertSettings) -> AlertSettingsOut:
    return AlertSettingsOut(
        recipients=parse_recipients(s.recipients),
        smtp_host=s.smtp_host,
        smtp_port=s.smtp_port,
        smtp_username=s.smtp_username,
        has_smtp_password=bool(s.encrypted_smtp_password),
        smtp_starttls=s.smtp_starttls,
        smtp_ssl=s.smtp_ssl,
        smtp_from=s.smtp_from,
        teams_webhook_host=_host(s.teams_webhook_url),
        slack_webhook_host=_host(s.slack_webhook_url),
        alert_config_change=s.alert_config_change,
        channels=alerting.channels(s),
    )


@router.get("/settings", response_model=AlertSettingsOut)
async def get_settings(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> AlertSettingsOut:
    settings = await _get_or_create(db, user.org_id)
    await db.commit()
    return _out(settings)


@router.put("/settings", response_model=AlertSettingsOut)
async def update_settings(payload: AlertSettingsUpdate, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> AlertSettingsOut:
    settings = await _get_or_create(db, admin.org_id)
    for field in ("smtp_host", "smtp_port", "smtp_username", "smtp_starttls", "smtp_ssl", "smtp_from", "alert_config_change"):
        setattr(settings, field, getattr(payload, field))
    settings.recipients = ", ".join(payload.recipients) or None
    # Webhook URLs: an omitted/blank value keeps the existing secret URL
    # (the form never receives it back), the literal "-" clears it.
    for field in ("teams_webhook_url", "slack_webhook_url"):
        value = getattr(payload, field)
        if value == "-":
            setattr(settings, field, None)
        elif value:
            setattr(settings, field, value)
    if payload.clear_smtp_password:
        settings.encrypted_smtp_password = None
    elif payload.smtp_password:
        settings.encrypted_smtp_password = encrypt_secret(payload.smtp_password)
    await db.commit()
    return _out(settings)


@router.post("/test", response_model=AlertTestResult)
async def test_channels(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> AlertTestResult:
    """Sends a test message through every configured channel using the
    *saved* settings."""
    settings = await _get_or_create(db, admin.org_id)
    await db.commit()
    if not alerting.channels(settings):
        return AlertTestResult(results={}, ok=False)
    org = await db.get(Organization, admin.org_id)
    results = await asyncio.to_thread(alerting.test_delivery, settings, org.name if org else "your organization")
    return AlertTestResult(results=results, ok=all(v == "ok" for v in results.values()))


@router.get("", response_model=list[SnmpAlertOut])
async def list_alerts(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[SnmpAlertOut]:
    rows = await db.scalars(
        select(SnmpAlert).where(SnmpAlert.org_id == user.org_id).order_by(SnmpAlert.created_at.desc(), SnmpAlert.id.desc()).limit(500)
    )
    return [
        SnmpAlertOut(
            id=a.id,
            device_id=a.device_id,
            device_name=a.device_name,
            kind=a.kind,
            kind_label=KIND_LABELS[a.kind],
            subject=a.subject,
            detail=a.detail,
            emailed=a.emailed,
            email_error=a.email_error,
            notified_via=a.notified_via,
            webhook_error=a.webhook_error,
            created_at=a.created_at,
        )
        for a in rows
    ]


@router.delete("", response_model=SnmpJobClearResult)
async def clear_alerts(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> SnmpJobClearResult:
    result = await db.execute(delete(SnmpAlert).where(SnmpAlert.org_id == admin.org_id))
    await db.commit()
    return SnmpJobClearResult(deleted=result.rowcount or 0)
