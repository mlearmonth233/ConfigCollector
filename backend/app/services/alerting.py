"""One place that delivers alerts, whoever raised them.

The SNMP monitor, the ping monitor and the config-change check all create
SnmpAlert rows (the org's single alert history) and call dispatch(), which
sends the batch through every channel configured in the org's
AlertSettings - email over SMTP, a Microsoft Teams incoming webhook, a
Slack incoming webhook - and records on each alert row which channels
worked and what went wrong on the ones that did not. Delivery problems are
recorded, never raised: a broken SMTP server must not stop a monitoring
cycle.
"""

from __future__ import annotations

import logging

import httpx

from app.core.encryption import decrypt_secret
from app.core.licence import PAID_TIER_LABELS, upgrade_message
from app.models.alerting import AlertSettings
from app.models.organization import Organization
from app.models.snmp_monitor import SnmpAlert
from app.services import snmp_monitor
from app.services.licensing import get_licence_sync
from app.services.snmp_monitor import KIND_LABELS, Event, SmtpSettings, format_email, parse_recipients

log = logging.getLogger(__name__)

WEBHOOK_TIMEOUT = 10.0


def get_or_create_settings(db, org_id) -> AlertSettings:
    """Sync-session variant (Celery tasks)."""
    settings = db.query(AlertSettings).filter(AlertSettings.org_id == org_id).first()
    if settings is None:
        settings = AlertSettings(org_id=org_id)
        db.add(settings)
        db.flush()
    return settings


def smtp_settings(settings: AlertSettings | None) -> SmtpSettings | None:
    if settings is None or not settings.smtp_host or not parse_recipients(settings.recipients):
        return None
    return SmtpSettings(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=decrypt_secret(settings.encrypted_smtp_password) if settings.encrypted_smtp_password else None,
        starttls=settings.smtp_starttls,
        ssl=settings.smtp_ssl,
        sender=settings.smtp_from,
    )


def channels(settings: AlertSettings | None) -> list[str]:
    """Names of the delivery channels that are configured."""
    if settings is None:
        return []
    found = []
    if smtp_settings(settings) is not None:
        found.append("email")
    if settings.teams_webhook_url:
        found.append("teams")
    if settings.slack_webhook_url:
        found.append("slack")
    return found


def teams_payload(subject: str, body: str) -> dict:
    """An Adaptive Card, which both Teams Workflows webhooks and the older
    Office 365 connectors accept."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "msteams": {"width": "Full"},
                    "body": [
                        {"type": "TextBlock", "text": subject, "weight": "Bolder", "size": "Medium", "wrap": True},
                        {"type": "TextBlock", "text": body, "wrap": True, "fontType": "Monospace"},
                    ],
                },
            }
        ],
    }


def slack_payload(subject: str, body: str) -> dict:
    return {"text": f"*{subject}*\n```{body}```"}


def post_webhook(url: str, payload: dict, *, client: httpx.Client | None = None) -> None:
    """POSTs JSON; raises on any non-2xx so the caller can record it."""
    own = client is None
    client = client or httpx.Client(timeout=WEBHOOK_TIMEOUT)
    try:
        response = client.post(url, json=payload)
        response.raise_for_status()
    finally:
        if own:
            client.close()


def dispatch(db, org_id, batch: list[tuple[str, Event]], alerts: list[SnmpAlert], *, webhook_client: httpx.Client | None = None) -> str:
    """Delivers `batch` (device name, event) through every configured
    channel and marks each of `alerts` accordingly. Returns a short summary
    suffix for the caller's cycle result, e.g. ", emailed 2 recipient(s),
    posted to teams" or ", not delivered (no channels configured)"."""
    licence = get_licence_sync(db, org_id)
    if not licence.has("alert_delivery"):
        # Alerts are still recorded (the history is free); sending them is not.
        for alert in alerts:
            alert.email_error = upgrade_message("alert_delivery", licence)
        return f", not delivered (alert delivery needs {PAID_TIER_LABELS})"
    settings = db.query(AlertSettings).filter(AlertSettings.org_id == org_id).first()
    configured = channels(settings)
    if not configured:
        for alert in alerts:
            alert.email_error = "No delivery channel configured (Alerts page)"
        return ", not delivered (no channels configured)"

    org = db.get(Organization, org_id)
    subject, body = format_email(org.name if org else "your organization", batch)
    delivered: list[str] = []
    problems: list[str] = []

    smtp = smtp_settings(settings)
    if smtp is not None:
        recipients = parse_recipients(settings.recipients)
        try:
            snmp_monitor.send_email(smtp, recipients, subject, body)
            delivered.append("email")
            for alert in alerts:
                alert.emailed = True
        except Exception as exc:  # noqa: BLE001
            log.error("Alert email via %s FAILED for org %s: %s", settings.smtp_host, org_id, exc)
            problems.append(f"email: {exc}")
            for alert in alerts:
                alert.email_error = str(exc)[:1000]

    for name, url, payload in (
        ("teams", settings.teams_webhook_url, teams_payload(subject, body)),
        ("slack", settings.slack_webhook_url, slack_payload(subject, body)),
    ):
        if not url:
            continue
        try:
            post_webhook(url, payload, client=webhook_client)
            delivered.append(name)
        except Exception as exc:  # noqa: BLE001
            log.error("Alert %s webhook FAILED for org %s: %s", name, org_id, exc)
            problems.append(f"{name}: {exc}")

    for alert in alerts:
        alert.notified_via = ", ".join(delivered) or None
        webhook_problems = [p for p in problems if not p.startswith("email:")]
        alert.webhook_error = "; ".join(webhook_problems)[:1000] or None

    parts = []
    if "email" in delivered:
        parts.append(f"emailed {len(parse_recipients(settings.recipients))} recipient(s)")
    posted = [d for d in delivered if d != "email"]
    if posted:
        parts.append("posted to " + " and ".join(posted))
    if problems:
        parts.append("DELIVERY FAILED: " + "; ".join(problems))
    return ", " + ", ".join(parts) if parts else ""


def test_delivery(settings: AlertSettings, org_name: str, *, webhook_client: httpx.Client | None = None) -> dict[str, str]:
    """Sends a test message on every configured channel. Returns
    {channel: 'ok' | error text}."""
    results: dict[str, str] = {}
    subject = "[Packrat] Test alert"
    body = f"This is a test from Packrat for {org_name}. If you can read this, alerts will reach you here."
    smtp = smtp_settings(settings)
    if smtp is not None:
        try:
            snmp_monitor.send_email(smtp, parse_recipients(settings.recipients), subject, body)
            results["email"] = "ok"
        except Exception as exc:  # noqa: BLE001
            results["email"] = str(exc)
    for name, url, payload in (
        ("teams", settings.teams_webhook_url, teams_payload(subject, body)),
        ("slack", settings.slack_webhook_url, slack_payload(subject, body)),
    ):
        if not url:
            continue
        try:
            post_webhook(url, payload, client=webhook_client)
            results[name] = "ok"
        except Exception as exc:  # noqa: BLE001
            results[name] = str(exc)
    return results


__all__ = ["KIND_LABELS", "Event", "channels", "dispatch", "get_or_create_settings", "smtp_settings", "test_delivery"]
