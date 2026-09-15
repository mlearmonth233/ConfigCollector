import pytest
from httpx import AsyncClient

from app import tasks as tasks_module
from app.services.dns_check import DnsCheckResult

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "DnsCheckOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _fake_run_dns_checks(targets: list[str]) -> list[DnsCheckResult]:
    return [
        DnsCheckResult(
            target=t,
            ping_ok=(t == "core-sw1"),
            forward_ok=True,
            forward_ips=["10.0.0.5"],
            reverse_ok=True,
            reverse_hostname="core-sw1.example.com",
        )
        for t in targets
    ]


async def test_create_dns_check_job_runs_to_completion(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    monkeypatch.setattr(tasks_module, "run_dns_checks", _fake_run_dns_checks)

    resp = await client.post(
        "/api/dns-check/jobs", headers=_auth(token), json={"targets": ["core-sw1", "10.0.0.9"]}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["item_count"] == 2
    by_target = {i["target"]: i for i in body["items"]}
    assert by_target["core-sw1"]["status"] == "completed"
    assert by_target["core-sw1"]["ping_ok"] is True
    assert by_target["core-sw1"]["forward_ips"] == ["10.0.0.5"]
    assert by_target["core-sw1"]["reverse_hostname"] == "core-sw1.example.com"
    assert by_target["10.0.0.9"]["ping_ok"] is False


async def test_create_dns_check_job_dedupes_and_trims_targets(client: AsyncClient, unique_email, monkeypatch):
    seen_targets = None

    async def fake_run_dns_checks(targets: list[str]) -> list[DnsCheckResult]:
        nonlocal seen_targets
        seen_targets = targets
        return [
            DnsCheckResult(target=t, ping_ok=True, forward_ok=False, forward_ips=[], reverse_ok=False, reverse_hostname=None)
            for t in targets
        ]

    monkeypatch.setattr(tasks_module, "run_dns_checks", fake_run_dns_checks)
    token = await _register(client, unique_email)

    resp = await client.post(
        "/api/dns-check/jobs",
        headers=_auth(token),
        json={"targets": [" core-sw1 ", "core-sw1", "", "  ", "core-sw2"]},
    )
    assert resp.status_code == 201, resp.text
    assert seen_targets == ["core-sw1", "core-sw2"]
    assert resp.json()["item_count"] == 2


async def test_create_dns_check_job_rejects_empty_target_list(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post("/api/dns-check/jobs", headers=_auth(token), json={"targets": ["", "   "]})
    assert resp.status_code == 400
    assert "at least one" in resp.json()["detail"]


async def test_create_dns_check_job_rejects_too_many_targets(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post(
        "/api/dns-check/jobs",
        headers=_auth(token),
        json={"targets": [f"host{i}" for i in range(10001)]},
    )
    assert resp.status_code == 400
    assert "Too many targets" in resp.json()["detail"]


async def test_dns_check_job_requires_login(client: AsyncClient):
    resp = await client.post("/api/dns-check/jobs", json={"targets": ["core-sw1"]})
    assert resp.status_code == 401


async def test_list_and_get_dns_check_job(client: AsyncClient, unique_email, monkeypatch):
    monkeypatch.setattr(tasks_module, "run_dns_checks", _fake_run_dns_checks)
    token = await _register(client, unique_email)

    created = await client.post(
        "/api/dns-check/jobs", headers=_auth(token), json={"targets": ["core-sw1"]}
    )
    job_id = created.json()["id"]

    listed = await client.get("/api/dns-check/jobs", headers=_auth(token))
    assert listed.status_code == 200
    assert any(j["id"] == job_id for j in listed.json())

    detail = await client.get(f"/api/dns-check/jobs/{job_id}", headers=_auth(token))
    assert detail.status_code == 200
    assert detail.json()["id"] == job_id
    assert detail.json()["items"][0]["target"] == "core-sw1"


async def test_get_dns_check_job_scoped_to_caller_org(client: AsyncClient, unique_email, monkeypatch):
    monkeypatch.setattr(tasks_module, "run_dns_checks", _fake_run_dns_checks)
    token_a = await _register(client, unique_email, org_name="OrgA")
    token_b = await _register(client, f"other-{unique_email}", org_name="OrgB")

    created = await client.post(
        "/api/dns-check/jobs", headers=_auth(token_a), json={"targets": ["core-sw1"]}
    )
    job_id = created.json()["id"]

    resp = await client.get(f"/api/dns-check/jobs/{job_id}", headers=_auth(token_b))
    assert resp.status_code == 404


async def test_cancel_dns_check_job_marks_pending_items_cancelled(client: AsyncClient, unique_email, monkeypatch):
    # Task dispatch itself is disabled here (unlike the other tests) so the
    # job stays RUNNING with PENDING items, exactly as it would between a
    # real create and a real worker picking it up - the job's own status
    # only ever transitions away from RUNNING via the task's own
    # finalization (see run_dns_check_job_task), same as CollectionJob.
    monkeypatch.setattr(tasks_module.run_dns_check_job_task, "apply_async", lambda *a, **k: None)
    token = await _register(client, unique_email)

    created = await client.post(
        "/api/dns-check/jobs", headers=_auth(token), json={"targets": ["core-sw1", "core-sw2"]}
    )
    job_id = created.json()["id"]
    assert created.json()["status"] == "running"
    assert all(i["status"] == "pending" for i in created.json()["items"])

    resp = await client.post(f"/api/dns-check/jobs/{job_id}/cancel", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert all(i["status"] == "cancelled" for i in resp.json()["items"])


async def test_cancel_already_finished_dns_check_job_fails(client: AsyncClient, unique_email, monkeypatch):
    monkeypatch.setattr(tasks_module, "run_dns_checks", _fake_run_dns_checks)
    token = await _register(client, unique_email)

    # Fake resolves instantly, so under eager dispatch the job is already
    # finished by the time the create call returns.
    created = await client.post("/api/dns-check/jobs", headers=_auth(token), json={"targets": ["core-sw1"]})
    assert created.json()["status"] == "completed"

    resp = await client.post(f"/api/dns-check/jobs/{created.json()['id']}/cancel", headers=_auth(token))
    assert resp.status_code == 400
    assert "already finished" in resp.json()["detail"]


async def test_delete_and_clear_finished_dns_check_jobs(client: AsyncClient, unique_email, monkeypatch):
    monkeypatch.setattr(tasks_module, "run_dns_checks", _fake_run_dns_checks)
    token = await _register(client, unique_email)

    created = await client.post(
        "/api/dns-check/jobs", headers=_auth(token), json={"targets": ["core-sw1"]}
    )
    job_id = created.json()["id"]
    assert created.json()["status"] == "completed"

    resp = await client.delete(f"/api/dns-check/jobs/{job_id}", headers=_auth(token))
    assert resp.status_code == 204

    resp = await client.get(f"/api/dns-check/jobs/{job_id}", headers=_auth(token))
    assert resp.status_code == 404


async def test_clear_finished_dns_check_jobs(client: AsyncClient, unique_email, monkeypatch):
    monkeypatch.setattr(tasks_module, "run_dns_checks", _fake_run_dns_checks)
    token = await _register(client, unique_email)

    await client.post("/api/dns-check/jobs", headers=_auth(token), json={"targets": ["core-sw1"]})
    await client.post("/api/dns-check/jobs", headers=_auth(token), json={"targets": ["core-sw2"]})

    resp = await client.delete("/api/dns-check/jobs", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 2

    listed = await client.get("/api/dns-check/jobs", headers=_auth(token))
    assert listed.json() == []
