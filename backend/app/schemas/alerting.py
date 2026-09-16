from pydantic import BaseModel, Field, field_validator


class AlertSettingsUpdate(BaseModel):
    """The Alerts page form. `smtp_password` is write-only and only replaced
    when supplied non-empty; `clear_smtp_password` removes it."""

    recipients: list[str] = []
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    clear_smtp_password: bool = False
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_from: str | None = None
    teams_webhook_url: str | None = Field(default=None, max_length=1024)
    slack_webhook_url: str | None = Field(default=None, max_length=1024)
    alert_config_change: bool = True

    @field_validator("recipients")
    @classmethod
    def _emails(cls, value: list[str]) -> list[str]:
        cleaned = []
        for raw in value:
            addr = raw.strip()
            if not addr:
                continue
            if "@" not in addr or addr.startswith("@") or addr.endswith("@") or " " in addr:
                raise ValueError(f"'{addr}' doesn't look like an email address")
            cleaned.append(addr)
        return cleaned

    @field_validator("smtp_host", "smtp_username", "smtp_from", "teams_webhook_url", "slack_webhook_url")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("teams_webhook_url", "slack_webhook_url")
    @classmethod
    def _https(cls, value: str | None) -> str | None:
        # "-" is the form's "clear this webhook" signal (see api/alerts.py).
        if value is not None and value != "-" and not value.lower().startswith("https://"):
            raise ValueError("Webhook URLs must start with https://")
        return value


class AlertSettingsOut(BaseModel):
    recipients: list[str]
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    has_smtp_password: bool
    smtp_starttls: bool
    smtp_ssl: bool
    smtp_from: str | None
    # Webhook URLs contain a secret token; only whether one is set and its
    # host are returned, never the full URL.
    teams_webhook_host: str | None
    slack_webhook_host: str | None
    alert_config_change: bool
    channels: list[str]  # which of email / teams / slack are configured


class AlertTestResult(BaseModel):
    results: dict[str, str]  # channel -> "ok" or the error
    ok: bool
