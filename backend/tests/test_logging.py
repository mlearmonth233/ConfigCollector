"""File logging: every process writes to its own rotating file, requests
and errors are recorded, and admins can view/download the files."""

import io
import logging
import zipfile
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.config import get_settings
from app.core import logging_config
from app.core.logging_config import list_log_files, log_file_for, tail_file
from app.main import app

pytestmark = pytest.mark.asyncio


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: AsyncClient, email: str, org: str = "Log Org") -> str:
    resp = await client.post("/api/auth/register", json={"email": email, "password": "secret123", "org_name": org})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


async def _member_token(client: AsyncClient, admin_token: str, email: str) -> str:
    resp = await client.post("/api/users", headers=_auth(admin_token), json={"email": email, "password": "secret123", "role": "member"})
    assert resp.status_code == 201, resp.text
    login = await client.post("/api/auth/login", json={"email": email, "password": "secret123"})
    return login.json()["access_token"]


def _api_log() -> str:
    path = log_file_for("api")
    for handler in logging.getLogger().handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


def test_api_process_logs_to_its_own_file_in_the_configured_directory():
    path = log_file_for("api")
    assert path.parent == Path(get_settings().log_dir).resolve()
    assert path.name == "packrat-api.log"
    assert path.exists()
    text = _api_log()
    assert "Logging started for api process" in text
    assert "[api:" in text  # process name + pid in every line


def test_configure_logging_is_idempotent_and_does_not_duplicate_handlers():
    before = len(logging.getLogger().handlers)
    logging_config.configure_logging("api")
    assert len(logging.getLogger().handlers) == before


def test_tail_file_returns_the_last_lines(tmp_path):
    path = tmp_path / "x.log"
    path.write_text("\n".join(f"line {i}" for i in range(1000)) + "\n", encoding="utf-8")
    tail = tail_file(path, 3)
    assert tail.splitlines() == ["line 997", "line 998", "line 999"]
    # Reading only the end of a big file drops the (likely partial) first line.
    tail = tail_file(path, 5000, max_bytes=100)
    assert 5 <= len(tail.splitlines()) <= 12 and tail.splitlines()[-1] == "line 999"


async def test_requests_and_refusals_are_logged_with_the_caller(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    me = (await client.get("/api/auth/me", headers=_auth(token))).json()
    await client.post("/api/devices", headers=_auth(token), json={"name": "log-sw1", "host": "192.0.2.10", "device_type": "cisco_ios"})
    refused = await client.post("/api/devices", headers=_auth(token), json={"name": "log-sw1", "host": "192.0.2.10", "device_type": "not_a_type"})
    assert refused.status_code in (400, 404, 422)
    bad_login = await client.post("/api/auth/login", json={"email": unique_email, "password": "wrong"})
    assert bad_login.status_code == 401

    text = _api_log()
    assert f"POST /api/devices -> 201" in text
    assert f"user={me['id']}" in text
    assert f"Login failed for {unique_email} from 127.0.0.1 (wrong password)" in text
    assert f"User {unique_email} ({me['id']}) logged in" not in text  # register isn't a login
    assert "refused with" in text or "rejected as invalid (422)" in text


async def test_unhandled_exception_returns_a_reference_that_is_in_the_log(client: AsyncClient):
    @app.get("/api/_test/boom")
    async def _boom():
        raise RuntimeError("kaboom for the log test")

    try:
        resp = await client.get("/api/_test/boom")
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != "/api/_test/boom"]
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"].startswith("Internal server error (ref ")
    ref = body["ref"]
    text = _api_log()
    assert f"ref={ref}" in text
    assert "RuntimeError: kaboom for the log test" in text
    assert "Traceback (most recent call last)" in text


async def test_logs_overview_tail_and_download_are_admin_only(client: AsyncClient, unique_email):
    admin = await _register(client, unique_email)
    member = await _member_token(client, admin, f"member-{unique_email}")

    assert (await client.get("/api/logs", headers=_auth(member))).status_code == 403
    assert (await client.get("/api/logs/download", headers=_auth(member))).status_code == 403
    assert (await client.get("/api/logs")).status_code == 401

    overview = await client.get("/api/logs", headers=_auth(admin))
    assert overview.status_code == 200, overview.text
    data = overview.json()
    assert data["enabled"] is True and data["level"] == "INFO"
    names = [f["name"] for f in data["files"]]
    assert "packrat-api.log" in names
    assert all(f["size_bytes"] > 0 for f in data["files"])

    tail = await client.get("/api/logs/tail", headers=_auth(admin), params={"name": "packrat-api.log", "lines": 50})
    assert tail.status_code == 200
    assert tail.json()["name"] == "packrat-api.log"
    assert 1 <= tail.json()["lines"] <= 50
    assert "[api:" in tail.json()["content"]

    # No escaping the log directory.
    for bad in ("../conftest.py", "..\\x", ".hidden", "nope.log"):
        assert (await client.get("/api/logs/tail", headers=_auth(admin), params={"name": bad})).status_code == 404

    download = await client.get("/api/logs/download", headers=_auth(admin))
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert download.headers["content-disposition"].startswith('attachment; filename="packrat-logs-')
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        members = archive.namelist()
        assert "system-info.txt" in members and "packrat-api.log" in members
        info = archive.read("system-info.txt").decode()
        assert "Database: sqlite+aiosqlite" in info and "Python:" in info
        assert "change-me" not in info  # no secrets in the bundle


async def test_log_access_can_be_disabled_by_setting(client: AsyncClient, unique_email, monkeypatch):
    admin = await _register(client, unique_email)
    monkeypatch.setattr(get_settings(), "log_download_enabled", False)
    overview = await client.get("/api/logs", headers=_auth(admin))
    assert overview.status_code == 200 and overview.json()["enabled"] is False and overview.json()["files"] == []
    assert (await client.get("/api/logs/tail", headers=_auth(admin), params={"name": "packrat-api.log"})).status_code == 403
    assert (await client.get("/api/logs/download", headers=_auth(admin))).status_code == 403


async def test_browser_errors_are_recorded_in_the_api_log(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post(
        "/api/logs/client",
        headers={**_auth(token), "User-Agent": "TestBrowser/1.0"},
        json={"message": "TypeError: x is undefined", "source": "http://localhost:5173/devices", "stack": "at Devices.tsx:42", "kind": "error"},
    )
    assert resp.status_code == 204
    assert (await client.post("/api/logs/client", json={"message": "anon"})).status_code == 401
    text = _api_log()
    assert "Browser error on http://localhost:5173/devices" in text
    assert "TestBrowser/1.0" in text and "TypeError: x is undefined" in text and "at Devices.tsx:42" in text


async def test_worker_side_events_are_logged(client: AsyncClient, unique_email, monkeypatch):
    """Eager mode runs the worker's tasks inside the API process, so their
    lines land in the api log - the same lines a real worker writes to
    packrat-worker.log."""
    from app import tasks as tasks_module
    from app.services.collector import AuthenticationError

    token = await _register(client, unique_email)
    await client.post("/api/credentials", headers=_auth(token), json={"name": "cred", "username": "u", "password": "p", "is_default": True})
    dev = await client.post("/api/devices", headers=_auth(token), json={"name": "log-core", "host": "192.0.2.20", "device_type": "cisco_ios"})

    def fail(*_args, **_kwargs):
        raise AuthenticationError("Authentication was rejected for 192.0.2.20:22: bad password")

    monkeypatch.setattr(tasks_module, "_attempt_collection", fail)
    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [dev.json()["id"]]})
    assert job.status_code == 201, job.text
    text = _api_log()
    assert "Collecting log-core (192.0.2.20, cisco_ios)" in text
    assert "Collection of log-core (192.0.2.20) FAILED: Authentication was rejected" in text
    assert "Task app.tasks.collect_device_task started" in text and "finished" in text


def test_list_log_files_only_returns_packrat_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path))
    (tmp_path / "packrat-api.log").write_text("a")
    (tmp_path / "packrat-worker.log.1").write_text("b")
    (tmp_path / "other.txt").write_text("c")
    (tmp_path / "configcollector.db").write_text("d")
    assert sorted(p.name for p in list_log_files()) == ["packrat-api.log", "packrat-worker.log.1"]
