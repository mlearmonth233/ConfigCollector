"""Start-up secret checks and login brute-force protection."""

import pytest
from httpx import AsyncClient

from app import config
from app.api import auth as auth_module
from app.core.login_guard import LoginGuard

pytestmark = pytest.mark.asyncio


# --- secrets ----------------------------------------------------------------------------


def _settings(**overrides):
    base = dict(environment="production", jwt_secret_key="x" * 48, credential_encryption_key="k" * 44)
    base.update(overrides)
    return config.Settings(**base)


def test_insecure_secrets_are_detected():
    assert config.insecure_secrets(_settings()) == []
    assert config.insecure_secrets(_settings(jwt_secret_key=config.INSECURE_JWT_SECRET)) == ["JWT_SECRET_KEY"]
    assert config.insecure_secrets(_settings(jwt_secret_key="short")) == ["JWT_SECRET_KEY"]
    assert config.insecure_secrets(_settings(credential_encryption_key=config.INSECURE_ENCRYPTION_KEY)) == ["CREDENTIAL_ENCRYPTION_KEY"]
    assert config.insecure_secrets(_settings(jwt_secret_key=config.INSECURE_JWT_SECRET, credential_encryption_key=config.INSECURE_ENCRYPTION_KEY)) == [
        "JWT_SECRET_KEY",
        "CREDENTIAL_ENCRYPTION_KEY",
    ]


def test_placeholder_secrets_refuse_to_start_outside_development():
    with pytest.raises(RuntimeError) as excinfo:
        config.assert_secrets_configured(_settings(jwt_secret_key=config.INSECURE_JWT_SECRET))
    message = str(excinfo.value)
    assert "JWT_SECRET_KEY" in message and "ENVIRONMENT='production'" in message and "Fernet" in message


def test_placeholder_secrets_only_warn_in_development(caplog):
    with caplog.at_level("WARNING", logger="app"):
        config.assert_secrets_configured(_settings(environment="development", credential_encryption_key=config.INSECURE_ENCRYPTION_KEY))
    assert "placeholder CREDENTIAL_ENCRYPTION_KEY" in caplog.text
    config.assert_secrets_configured(_settings())  # real secrets: silent


# --- login guard (unit) -------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_login_guard_locks_after_threshold_and_escalates():
    clock = Clock()
    g = LoginGuard(max_failures=3, window_seconds=600, lockout_seconds=30, lockout_max_seconds=120, clock=clock)
    assert g.record_failure("ip:1") == 0
    assert g.record_failure("ip:1") == 0
    assert g.retry_after("ip:1") == 0
    assert g.record_failure("ip:1") == 30  # third failure -> 30s
    assert g.retry_after("ip:1") == 30
    clock.t += 31
    assert g.retry_after("ip:1") == 0
    # Next round escalates: 60s, then capped at 120.
    for _ in range(2):
        g.record_failure("ip:1")
    assert g.record_failure("ip:1") == 60
    clock.t += 61
    for _ in range(2):
        g.record_failure("ip:1")
    assert g.record_failure("ip:1") == 120
    clock.t += 121
    for _ in range(2):
        g.record_failure("ip:1")
    assert g.record_failure("ip:1") == 120  # capped
    # Success clears everything.
    g.record_success("ip:1")
    assert g.retry_after("ip:1") == 0 and g.record_failure("ip:1") == 0


def test_login_guard_window_expiry_and_sweep():
    clock = Clock()
    g = LoginGuard(max_failures=3, window_seconds=60, lockout_seconds=30, clock=clock)
    g.record_failure("account:a")
    g.record_failure("account:a")
    clock.t += 61  # both failures fall out of the window
    assert g.record_failure("account:a") == 0
    g.sweep()
    clock.t += 61
    g.sweep()
    assert g._records == {}  # noqa: SLF001 - checking the table is bounded


def test_login_guard_tracks_ip_and_account_independently():
    g = LoginGuard(max_failures=2, lockout_seconds=30, clock=Clock())
    g.record_failure("ip:1", "account:alice")
    assert g.record_failure("ip:1", "account:bob") == 30  # ip crosses first
    assert g.retry_after("ip:1") == 30 and g.retry_after("account:alice") == 0 and g.retry_after("account:bob") == 0
    assert g.retry_after("ip:2", "account:alice") == 0


# --- login endpoint ----------------------------------------------------------------------


async def test_login_locks_out_after_repeated_failures(client: AsyncClient, unique_email, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(auth_module, "guard", LoginGuard(max_failures=3, lockout_seconds=30, clock=clock))
    reg = await client.post("/api/auth/register", json={"email": unique_email, "password": "correct-horse", "org_name": "Guard Org"})
    assert reg.status_code == 201

    for _ in range(3):
        bad = await client.post("/api/auth/login", json={"email": unique_email, "password": "wrong"})
        assert bad.status_code == 401
    # Locked: even the right password is refused, with a Retry-After.
    locked = await client.post("/api/auth/login", json={"email": unique_email, "password": "correct-horse"})
    assert locked.status_code == 429
    assert locked.headers["retry-after"] == "30"
    assert "Try again in 30 seconds" in locked.json()["detail"]

    clock.t += 31
    ok = await client.post("/api/auth/login", json={"email": unique_email, "password": "correct-horse"})
    assert ok.status_code == 200 and "access_token" in ok.json()
    # Success cleared the counters: a fresh mistake is not an instant lockout.
    assert (await client.post("/api/auth/login", json={"email": unique_email, "password": "wrong"})).status_code == 401


async def test_lockout_is_per_account_across_unknown_emails_too(client: AsyncClient, unique_email, monkeypatch):
    """Guessing at a non-existent account counts against the IP as well, so
    enumeration attempts hit the same wall."""
    clock = Clock()
    monkeypatch.setattr(auth_module, "guard", LoginGuard(max_failures=2, lockout_seconds=30, clock=clock))
    for name in ("nobody1", "nobody2"):
        assert (await client.post("/api/auth/login", json={"email": f"{name}-{unique_email}", "password": "x"})).status_code == 401
    assert (await client.post("/api/auth/login", json={"email": f"nobody3-{unique_email}", "password": "x"})).status_code == 429
