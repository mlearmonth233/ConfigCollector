import io
import uuid
import zipfile
from datetime import date, datetime, timezone

import pytest
from httpx import AsyncClient

from app.services import log_archive as la


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post("/api/auth/register", json={"org_name": "LogOrg", "email": email, "password": "password123"})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def test_save_list_resolve_and_purge():
    org = uuid.uuid4()
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    first = la.save(org, "collection", "wlc 01/a", "hello\n", when)
    second = la.save(org, "collection", "wlc 01/a", "again\n", when)
    assert first is not None and second is not None and first != second
    la.save(org, "audit", "wireless-audit", "audit\n")
    entries = la.entries(org)
    assert len(entries) == 3 and entries[0].kind == "audit"  # newest day first
    old = [e for e in entries if e.kind == "collection"]
    assert {e.name for e in old} == {"wlc-01-a", "wlc-01-a-2"}
    assert la.resolve(org, old[0].path).read_text() in ("hello\n", "again\n")
    # Nothing outside the org's own folder.
    assert la.resolve(org, "../x") is None and la.resolve(org, f"{old[0].day}/../../x") is None
    assert la.resolve(uuid.uuid4(), old[0].path) is None

    assert la.purge(org, 30, today=date(2026, 3, 1)) == 2
    assert [e.kind for e in la.entries(org)] == ["audit"]
    assert la.delete(org, [la.entries(org)[0].path]) == 1
    assert la.entries(org) == []


def test_device_run_text():
    text = la.device_run_text(
        title="collection", job_name="Nightly", job_id="j1", device_name="wlc1", host="192.0.2.1", device_type="cisco_wlc",
        status="completed", started_at=None, finished_at=None, error=None, transcript="Connecting...\n", content="! ---- show sysinfo ----\nx",
    )
    assert "Device:    wlc1 (192.0.2.1, cisco_wlc)" in text and "===== Session log =====\nConnecting" in text
    assert text.endswith("x\n") and "===== Collected output =====" in text


@pytest.mark.asyncio
async def test_collections_are_saved_to_the_log_history(client: AsyncClient, unique_email, monkeypatch):
    from netmiko.exceptions import NetmikoAuthenticationException

    from app.services import collector as collector_module

    def reject(**kwargs):
        raise NetmikoAuthenticationException("Authentication to device failed.")

    monkeypatch.setattr(collector_module, "ConnectHandler", reject)
    token = await _register(client, unique_email)
    cred = await client.post("/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "pw"})
    device = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "sw-log", "host": "192.0.2.1", "device_type": "cisco_ios", "credential_id": cred.json()["id"]}
    )
    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device.json()["id"]], "name": "Log test"})
    assert job.status_code == 201, job.text

    resp = await client.get("/api/log-history", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_files"] == 1 and body["retention_days"] == 30
    entry = body["files"][0]
    assert entry["kind"] == "collection" and entry["name"] == "sw-log"

    resp = await client.get("/api/log-history/file", params={"path": entry["path"]}, headers=_auth(token))
    content = resp.json()["content"]
    assert "Status:    failed" in content and "Job:       Log test" in content and "Authentication" in content
    resp = await client.get("/api/log-history/file", params={"path": "../../etc/passwd"}, headers=_auth(token))
    assert resp.status_code == 404
    assert (await client.get("/api/log-history", params={"kind": "audit"}, headers=_auth(token))).json()["files"] == []

    resp = await client.post("/api/log-history/download.zip", json={"paths": [entry["path"]]}, headers=_auth(token))
    assert zipfile.ZipFile(io.BytesIO(resp.content)).namelist() == [entry["path"]]

    # Another organization sees none of it.
    other = await _register(client, "other-" + unique_email)
    assert (await client.get("/api/log-history", headers=_auth(other))).json()["total_files"] == 0
    resp = await client.get("/api/log-history/file", params={"path": entry["path"]}, headers=_auth(other))
    assert resp.status_code == 404

    resp = await client.post("/api/log-history/purge", json={"older_than_days": 0}, headers=_auth(token))
    assert resp.json() == {"deleted": 1}
    assert (await client.get("/api/log-history", headers=_auth(token))).json()["total_files"] == 0
