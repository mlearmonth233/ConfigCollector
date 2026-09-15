import pytest
from httpx import AsyncClient

from app import tasks as tasks_module
from app.db_sync import SyncSessionLocal
from app.models.dns_check import DnsCheckJob
from app.models.job import JobStatus
from app.services.dns_check import DnsCheckResult

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "DnsCheckTaskOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_job_processes_more_targets_than_one_chunk(client: AsyncClient, unique_email, monkeypatch):
    # Chunk size 2 against 5 targets forces 3 chunks - proves the task
    # loops over every chunk rather than only ever handling the first one.
    monkeypatch.setattr(tasks_module, "DNS_CHECK_CHUNK_SIZE", 2)

    async def fake_run_dns_checks(targets: list[str]) -> list[DnsCheckResult]:
        return [
            DnsCheckResult(target=t, ping_ok=True, forward_ok=False, forward_ips=[], reverse_ok=False, reverse_hostname=None)
            for t in targets
        ]

    monkeypatch.setattr(tasks_module, "run_dns_checks", fake_run_dns_checks)
    token = await _register(client, unique_email)

    targets = [f"host{i}" for i in range(5)]
    resp = await client.post("/api/dns-check/jobs", headers=_auth(token), json={"targets": targets})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert len(body["items"]) == 5
    assert all(i["status"] == "completed" for i in body["items"])


async def test_job_stops_remaining_chunks_once_cancelled_mid_batch(client: AsyncClient, unique_email, monkeypatch):
    monkeypatch.setattr(tasks_module, "DNS_CHECK_CHUNK_SIZE", 2)
    token = await _register(client, unique_email)
    org_id = (await client.get("/api/auth/me", headers=_auth(token))).json()["org_id"]
    call_count = 0

    async def fake_run_dns_checks(targets: list[str]) -> list[DnsCheckResult]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Simulate a concurrent POST .../cancel arriving while this
            # first chunk is still in flight - a real request would go
            # through the API, but a raw sync-session update mimics that
            # same effect without needing actual concurrency in a test.
            # Scoped to this test's own org: a job left RUNNING forever by
            # another test (one that deliberately disables dispatch) would
            # otherwise make "the one RUNNING job" ambiguous.
            sync_db = SyncSessionLocal()
            try:
                job = (
                    sync_db.query(DnsCheckJob)
                    .filter(DnsCheckJob.org_id == org_id, DnsCheckJob.status == JobStatus.RUNNING)
                    .one()
                )
                job.cancel_requested = True
                sync_db.commit()
            finally:
                sync_db.close()
        return [
            DnsCheckResult(target=t, ping_ok=True, forward_ok=False, forward_ips=[], reverse_ok=False, reverse_hostname=None)
            for t in targets
        ]

    monkeypatch.setattr(tasks_module, "run_dns_checks", fake_run_dns_checks)

    targets = [f"host{i}" for i in range(6)]  # 3 chunks of 2
    resp = await client.post("/api/dns-check/jobs", headers=_auth(token), json={"targets": targets})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["status"] == "cancelled"
    completed = [i for i in body["items"] if i["status"] == "completed"]
    pending = [i for i in body["items"] if i["status"] == "pending"]
    # Only the first chunk (2 targets) got processed before the break.
    assert len(completed) == 2
    assert len(pending) == 4
    assert call_count == 1
