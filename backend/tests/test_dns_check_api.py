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


async def _fake_run_dns_checks(targets: list[str], concurrency: int = 25) -> list[DnsCheckResult]:
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

    async def fake_run_dns_checks(targets: list[str], concurrency: int = 25) -> list[DnsCheckResult]:
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


# --- subnet sweeps ---------------------------------------------------------------------------


async def test_subnet_preview_describes_the_sweep(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.get("/api/dns-check/subnet-preview", headers=_auth(token), params={"subnet": "10.20.30.0 255.255.255.0"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"network": "10.20.30.0/24", "netmask": "255.255.255.0", "usable": 254, "first": "10.20.30.1", "last": "10.20.30.254"}

    resp = await client.get("/api/dns-check/subnet-preview", headers=_auth(token), params={"subnet": "10.20.0.0/16"})
    assert resp.status_code == 400 and "65,534 usable addresses" in resp.json()["detail"]


async def test_subnet_sweep_checks_every_usable_address_in_order(client: AsyncClient, unique_email, monkeypatch):
    seen_concurrency = []

    async def fake(targets: list[str], concurrency: int = 25) -> list[DnsCheckResult]:
        seen_concurrency.append(concurrency)
        results = []
        for t in targets:
            last = int(t.rsplit(".", 1)[1])
            if last == 1:  # gateway: answers, PTR points back at it
                results.append(DnsCheckResult(t, True, True, [t], True, "gw.example.com"))
            elif last == 7:  # stale PTR: name now resolves elsewhere, nothing answers
                results.append(DnsCheckResult(t, False, True, ["10.9.9.9"], True, "old-printer.example.com"))
            else:
                results.append(DnsCheckResult(t, False, False, [], False, None))
        return results

    monkeypatch.setattr(tasks_module, "run_dns_checks", fake)
    token = await _register(client, unique_email)
    resp = await client.post("/api/dns-check/jobs", headers=_auth(token), json={"subnet": "192.168.50.1/28"})
    assert resp.status_code == 201, resp.text
    job = resp.json()
    assert job["subnet"] == "192.168.50.0/28" and job["status"] == "completed" and job["item_count"] == 14
    assert [i["target"] for i in job["items"]] == [f"192.168.50.{n}" for n in range(1, 15)]
    assert seen_concurrency and set(seen_concurrency) == {64}

    by_ip = {i["target"]: i for i in job["items"]}
    assert by_ip["192.168.50.1"]["dns_matches"] is True and by_ip["192.168.50.1"]["ping_ok"] is True
    assert by_ip["192.168.50.7"]["dns_matches"] is False and by_ip["192.168.50.7"]["reverse_hostname"] == "old-printer.example.com"
    assert by_ip["192.168.50.2"]["dns_matches"] is None

    listed = (await client.get("/api/dns-check/jobs", headers=_auth(token))).json()
    assert listed[0]["subnet"] == "192.168.50.0/28" and listed[0]["item_count"] == 14


async def test_subnet_sweep_rejects_bad_or_mixed_input(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post("/api/dns-check/jobs", headers=_auth(token), json={"subnet": "10.1.1.0"})
    assert resp.status_code == 400 and "has no mask" in resp.json()["detail"]
    resp = await client.post("/api/dns-check/jobs", headers=_auth(token), json={"subnet": "10.0.0.0/8"})
    assert resp.status_code == 400 and "more than" in resp.json()["detail"]
    resp = await client.post("/api/dns-check/jobs", headers=_auth(token), json={"subnet": "10.1.1.0/24", "targets": ["core-sw1"]})
    assert resp.status_code == 400 and "not both" in resp.json()["detail"]
    resp = await client.post("/api/dns-check/jobs", headers=_auth(token), json={})
    assert resp.status_code == 400 and "at least one" in resp.json()["detail"]
