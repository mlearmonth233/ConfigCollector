import pytest
from httpx import AsyncClient

from app.database import async_session_factory
from app.models.job import CollectionJob, CollectionJobItem, JobStatus
from app.models.snapshot import ConfigSnapshot

pytestmark = pytest.mark.asyncio

_CDP_BLOCK = (
    "! ---- show cdp neighbor detail ----\n"
    "-------------------------\n"
    "Device ID: SW02.corp.local\n"
    "Entry address(es):\n"
    "  IP address: 10.0.0.2\n"
    "Platform: cisco WS-C3850-24P,  Capabilities: Switch IGMP\n"
    "Interface: GigabitEthernet1/0/1,  Port ID (outgoing port): GigabitEthernet1/0/1\n"
    "Holdtime : 163 sec\n"
)

_LLDP_BLOCK = (
    "! ---- sh lldp nei detail ----\n"
    "------------------------------------------------\n"
    "Local Intf: Gi1/0/2\n"
    "Chassis id: aabb.ccdd.eeff\n"
    "System Name: SW02.corp.local\n"
    "Management Addresses:\n"
    "    IP: 10.0.0.2\n"
)

_NO_NEIGHBORS_CDP_BLOCK = "! ---- show cdp neighbor detail ----\nTotal cdp entries displayed : 0\n"


async def _register(client: AsyncClient, email: str, org_name: str = "NeighborOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_job(
    token: str, client: AsyncClient, devices: list[tuple[str, str, str]]
) -> str:
    """devices: list of (name, device_type, snapshot_content)."""
    me = await client.get("/api/auth/me", headers=_auth(token))
    org_id = me.json()["org_id"]
    user_id = me.json()["id"]

    device_ids = []
    for name, device_type, _content in devices:
        device = await client.post(
            "/api/devices",
            headers=_auth(token),
            json={"name": name, "host": "192.0.2.50", "device_type": device_type},
        )
        assert device.status_code == 201, device.text
        device_ids.append(device.json()["id"])

    async with async_session_factory() as db:
        job = CollectionJob(org_id=org_id, created_by_id=user_id, status=JobStatus.COMPLETED)
        db.add(job)
        await db.flush()
        for device_id, (_name, _device_type, content) in zip(device_ids, devices):
            item = CollectionJobItem(job_id=job.id, device_id=device_id, status=JobStatus.COMPLETED)
            db.add(item)
            await db.flush()
            db.add(ConfigSnapshot(device_id=device_id, job_item_id=item.id, content=content))
        await db.commit()
        job_id = str(job.id)
    return job_id


async def test_neighbor_gap_check_flags_untracked_neighbor(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    job_id = await _seed_job(token, client, [("core-sw1", "cisco_ios", _CDP_BLOCK)])

    resp = await client.get(f"/api/jobs/{job_id}/neighbor-gaps", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["checked_item_count"] == 1
    assert len(body["missing"]) == 1
    entry = body["missing"][0]
    assert entry["name"] == "SW02.corp.local"
    assert entry["ip"] == "10.0.0.2"
    assert entry["protocols"] == ["cdp"]
    assert entry["seen_from"] == ["core-sw1"]


async def test_neighbor_gap_check_excludes_devices_already_tracked(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    # SW02 already in inventory (name only, no domain suffix) - should
    # match the FQDN-reported neighbor "SW02.corp.local" via normalization.
    await client.post(
        "/api/devices", headers=_auth(token), json={"name": "SW02", "host": "192.0.2.2", "device_type": "cisco_ios"}
    )
    job_id = await _seed_job(token, client, [("core-sw1", "cisco_ios", _CDP_BLOCK)])

    resp = await client.get(f"/api/jobs/{job_id}/neighbor-gaps", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["missing"] == []


async def test_neighbor_gap_check_dedupes_across_devices_and_protocols(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    job_id = await _seed_job(
        token,
        client,
        [
            ("core-sw1", "cisco_ios", _CDP_BLOCK),
            ("core-sw2", "cisco_ios", _LLDP_BLOCK),
        ],
    )

    resp = await client.get(f"/api/jobs/{job_id}/neighbor-gaps", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["checked_item_count"] == 2
    assert len(body["missing"]) == 1
    entry = body["missing"][0]
    assert sorted(entry["protocols"]) == ["cdp", "lldp"]
    assert sorted(entry["seen_from"]) == ["core-sw1", "core-sw2"]


async def test_neighbor_gap_check_counts_zero_neighbors_as_checked(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    job_id = await _seed_job(token, client, [("core-sw1", "cisco_ios", _NO_NEIGHBORS_CDP_BLOCK)])

    resp = await client.get(f"/api/jobs/{job_id}/neighbor-gaps", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["checked_item_count"] == 1
    assert body["missing"] == []


async def test_neighbor_gap_check_excludes_devices_with_no_neighbor_commands(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    # A PDU never runs CDP/LLDP commands at all - shouldn't count as checked.
    job_id = await _seed_job(
        token, client, [("pdu1", "apc_pdu", "! ---- about ----\nfirmware v1.0\n")]
    )

    resp = await client.get(f"/api/jobs/{job_id}/neighbor-gaps", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["checked_item_count"] == 0


async def test_neighbor_gap_check_404_for_other_org(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email)
    job_id = await _seed_job(token_a, client, [("core-sw1", "cisco_ios", _CDP_BLOCK)])

    other_email = f"other-{unique_email}"
    token_b = await _register(client, other_email, org_name="OtherNeighborOrg")
    resp = await client.get(f"/api/jobs/{job_id}/neighbor-gaps", headers=_auth(token_b))
    assert resp.status_code == 404
