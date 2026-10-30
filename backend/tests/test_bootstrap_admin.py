"""BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD: a known admin login
created (or recovered) at API start-up from the machine's own settings."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.core.security import hash_password
from app.db_sync import SyncSessionLocal
from app.models.user import User, UserRole
from app.services.bootstrap_admin import ensure_bootstrap_admin

pytestmark = pytest.mark.asyncio


@pytest.fixture
def configure(monkeypatch):
    def _set(email: str = "", password: str = "", org: str = "MIL Networks Limited"):
        settings = get_settings()
        monkeypatch.setattr(settings, "bootstrap_admin_email", email)
        monkeypatch.setattr(settings, "bootstrap_admin_password", password)
        monkeypatch.setattr(settings, "bootstrap_admin_org_name", org)

    return _set


async def _login(client: AsyncClient, email: str, password: str) -> int:
    return (await client.post("/api/auth/login", json={"email": email, "password": password})).status_code


async def test_nothing_happens_when_not_configured(configure):
    configure()
    assert ensure_bootstrap_admin(SyncSessionLocal) is None


async def test_creates_an_admin_who_can_log_in(client: AsyncClient, configure, unique_email):
    configure(unique_email, "C!sco123")
    assert ensure_bootstrap_admin(SyncSessionLocal) == "created"
    assert await _login(client, unique_email, "C!sco123") == 200
    token = (await client.post("/api/auth/login", json={"email": unique_email, "password": "C!sco123"})).json()["access_token"]
    me = (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})).json()
    assert me["role"] == "admin" and me["email"] == unique_email
    # Running again changes nothing.
    assert ensure_bootstrap_admin(SyncSessionLocal) == "unchanged"


async def test_recovers_an_existing_account(client: AsyncClient, configure, unique_email):
    db = SyncSessionLocal()
    try:
        from app.models.organization import Organization

        org = Organization(name="Existing Org")
        db.add(org)
        db.flush()
        db.add(User(org_id=org.id, email=unique_email, hashed_password=hash_password("forgotten-1"), role=UserRole.MEMBER))
        db.commit()
    finally:
        db.close()

    # The configured email may differ in case from the stored one.
    configure(unique_email.upper(), "C!sco123")
    assert ensure_bootstrap_admin(SyncSessionLocal) == "updated"
    assert await _login(client, unique_email, "forgotten-1") == 401
    assert await _login(client, unique_email, "C!sco123") == 200

    db = SyncSessionLocal()
    try:
        users = db.scalars(select(User).where(User.email == unique_email)).all()
        assert len(users) == 1 and users[0].role == UserRole.ADMIN  # no duplicate, promoted to admin
    finally:
        db.close()


@pytest.mark.parametrize("email, password", [("someone@example.com", ""), ("", "C!sco123"), ("not-an-email", "C!sco123"), ("a@example.com", "short")])
async def test_unusable_settings_are_skipped(configure, email, password):
    configure(email, password)
    assert ensure_bootstrap_admin(SyncSessionLocal) is None
