"""Client history from controller logs: classification of joins, roams,
drops and failures per MAC, roam history and counters, the audit's client
events check, the MAC lookup API, and audits ignoring partial snapshots."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from app.database import async_session_factory
from app.models.job import CollectionJob, CollectionJobItem, JobStatus
from app.models.snapshot import ConfigSnapshot
from app.schemas.wireless_audit import WirelessStandard
from app.services import client_events as ce
from app.services import inventory as inv
from app.services import wireless_audit as wa

XE_LOG = """*Oct 15 10:00:01.100: %CLIENT_ORCH_LOG-6-CLIENT_ADDED_TO_RUN_STATE: Chassis 1 R0/0: wncd: Username entry (alice) joined with ssid (CORP) for device with MAC: 1111.2222.3333
*Oct 15 10:05:00.100: %CLIENT_ORCH_LOG-5-ROAM: Chassis 1 R0/0: wncd: Client 1111.2222.3333 roamed from AP-A to AP-B
*Oct 15 10:06:00.100: %CLIENT_ORCH_LOG-5-ROAM: Chassis 1 R0/0: wncd: Client 1111.2222.3333 roamed from AP-B to AP-A
*Oct 15 10:10:00.100: %CLIENT_ORCH_LOG-5-CLIENT_DELETED: Chassis 1 R0/0: wncd: Client 1111.2222.3333 deleted, reason: CO_CLIENT_DELETE_REASON_DOT11_DEAUTH
*Oct 15 10:11:00.100: %DOT1X-5-FAIL: Chassis 1 R0/0: sessmgrd: Authentication failed for client (4444.5555.6666) with reason (Timeout)
*Oct 15 10:12:00.100: %CLIENT_EXCLUSION_SERVER-5-ADD_TO_EXCLUSIONLIST_REASON: Client 4444.5555.6666 was added to exclusion list, reason: 802.1X authentication failure
*Oct 15 10:13:00.100: %CAPWAPAC_SMGR-4-AP_DISJOIN: AP a4b2.3c4d.5e60 disjoined
"""

AIREOS_MSGLOG = """*apfMsConnTask_0: Oct 15 10:00:00.123: %APF-6-CLIENT_ASSOC: Client a4:bb:6d:12:34:56 associated to AP-1
*Dot1x_NW_MsgTask_0: Oct 15 10:01:00.123: %DOT1X-3-MAX_EAP_RETRIES: Max EAP identity request retries (3) exceeded for client a4:bb:6d:12:34:56
"""

AIREOS_TRAPLOG = """Log System Time              Trap
--- ------------------------ -------------------------------------------------
  0 Thu Oct 15 10:02:00 2026 Client Deauthenticated: MACAddress:a4:bb:6d:12:34:56 Base Radio MAC:00:11:22:33:44:a0 Slot: 1 User Name: unknown Ip Address: 10.0.0.9 Reason:Unspecified ReasonCode: 1
  1 Thu Oct 15 10:03:00 2026 Client Association: Client MAC:a4:bb:6d:12:34:56 Base Radio MAC:00:11:22:33:44:a0
"""

MOBILITY = """Mobility History:

  IP              AP Name                         BSSID           Join Time                  Type            Roam Type
  --------------- ------------------------------- --------------- -------------------------- --------------- -----------------
  10.30.0.50      AP-A                            a4b2.3c4d.0061  10/15/2026 10:06:00 UTC    Local           802.11R
  10.30.0.50      AP-B                            a4b2.3c4d.0071  10/15/2026 10:05:00 UTC    Local           802.11R
  10.30.0.50      AP-A                            a4b2.3c4d.0061  10/15/2026 10:00:01 UTC    Local           Full Auth
"""

XE_DETAIL = """Client MAC Address : 1111.2222.3333
Client State : Associated
Policy Manager State: Run
Mobility Move Count : 2
Client Statistics:
  Number of Packets Sent : 1000
  Number of Data Retries : 400
  Number of Decrypt Failed Packets : 3
"""

DELETE_REASONS = """Total client delete reasons
---------------------------
No Operation                                    : 0
Deauthentication or disassociation request      : 12
Session timeout                                 : 4
Idle timeout                                    : 30
"""


def snapshot(sections: dict[str, str]) -> str:
    return "\n".join(f"! ---- {k} ----\n{v}" for k, v in sections.items())


def test_classify():
    assert ce.classify("Client deleted, reason: DOT11_DEAUTH")[0] == "drop"
    assert ce.classify("Client deleted, reason: DOT11_DEAUTH")[1] == "DOT11_DEAUTH"
    assert ce.classify("Authentication failed for client")[0] == "auth_failure"
    assert ce.classify("Max EAP identity request retries exceeded")[0] == "auth_failure"
    assert ce.classify("added to exclusion list")[0] == "exclusion"
    assert ce.classify("DHCP timeout for client")[0] == "dhcp_failure"
    assert ce.classify("Client roamed from AP-A to AP-B")[0] == "roam"
    assert ce.classify("idle timeout expired")[0] == "timeout"
    assert ce.classify("Client associated to AP-1")[0] == "join"


def test_histories_from_9800_logs_and_commands():
    content = snapshot(
        {
            "show logging": XE_LOG,
            "show wireless client mac-address 1111.2222.3333 detail": XE_DETAIL,
            "show wireless client mac-address 1111.2222.3333 mobility history": MOBILITY,
            "show wireless stats client delete reasons": DELETE_REASONS,
        }
    )
    hs = ce.histories(content, ignore_macs={"a4b2.3c4d.5e60"})
    assert set(hs) == {"1111.2222.3333", "4444.5555.6666"}  # the AP's MAC is not a client
    alice = hs["1111.2222.3333"]
    assert [e.kind for e in alice.events] == ["join", "roam", "roam", "drop"]
    assert alice.events[0].time == "Oct 15 10:00:01.100"
    assert [r["ap"] for r in alice.roams] == ["AP-A", "AP-B", "AP-A"]  # oldest first
    assert alice.roams[-1]["type"] == "802.11R"
    assert alice.counters == {"retries": 400, "packets_sent": 1000, "decrypt_failed": 3, "move_count": 2}
    assert alice.roam_count == 2 and alice.ping_pong() == ["AP-A ↔ AP-B"]
    issues = dict((m.split(":")[0].split(" ")[0], sev) for sev, m in ce.issues_for(alice, roam_warn=10, retry_warn_percent=25))
    messages = [m for _, m in ce.issues_for(alice, roam_warn=10, retry_warn_percent=25)]
    assert any(m.startswith("Dropped 1 time(s): CO_CLIENT_DELETE_REASON_DOT11_DEAUTH") for m in messages)
    assert any("Ping-pong" in m for m in messages) and any("40% data retries" in m for m in messages) and any("decrypt" in m for m in messages)
    assert issues
    bob = hs["4444.5555.6666"]
    assert bob.count("auth_failure") == 1 and bob.count("exclusion") == 1
    assert ce.parse_delete_reasons(DELETE_REASONS)[0] == {"reason": "Idle timeout", "count": 30}


def test_histories_from_aireos_logs():
    content = snapshot({"show msglog": AIREOS_MSGLOG, "show traplog": AIREOS_TRAPLOG})
    hs = ce.histories(content, ignore_macs={"0011.2233.44a0"})
    h = hs["a4bb.6d12.3456"]
    assert [e.kind for e in h.events] == ["join", "auth_failure", "drop", "join"]
    drop = next(e for e in h.events if e.kind == "drop")
    assert drop.time == "Thu Oct 15 10:02:00 2026" and drop.reason.startswith("Unspecified")
    assert "0011.2233.44a0" not in hs


def test_audit_reports_client_events_and_departed_clients():
    content = snapshot(
        {
            "show running-config": "hostname HQ-WLC01\n",
            "show wireless client summary": "MAC Address    AP Name  Type ID   State             Protocol Method     Role\n"
            "-----------\n1111.2222.3333 AP-A                                           WLAN 1    Run               11ax(5)  Dot1x      Local\n",
            "show logging": XE_LOG,
            "show wireless client mac-address 1111.2222.3333 mobility history": MOBILITY,
            "show wireless stats client delete reasons": DELETE_REASONS,
        }
    )
    inputs = [inv.SnapshotInput("x", "HQ-WLC01", "10.0.0.5", "cisco_wlc_9800", None, None, content, datetime.now(timezone.utc), "s")]
    report = wa.run_audit(wa.gather(inputs), WirelessStandard(), None)
    clients = {c["mac"]: c for c in report["clients"]}
    assert clients["1111.2222.3333"]["drops"] == 1 and clients["1111.2222.3333"]["roams"] == 2
    assert clients["4444.5555.6666"]["connected"] is False and clients["4444.5555.6666"]["state"] == "not associated"
    assert report["summary"]["clients"] == 1
    events = {e["mac"]: e for e in report["client_events"]}
    assert events["4444.5555.6666"]["exclusions"] == 1 and events["1111.2222.3333"]["roam_history"][0]["ap"] == "AP-A"
    found = [f for f in report["findings"] if f["check_id"] == "client.events"]
    assert any("no longer associated" in f["message"] for f in found)
    assert any(f["severity"] == "info" and "Idle timeout ×30" in f["message"] for f in found)
    assert report["client_health"][0]["delete_reasons"][0]["reason"] == "Idle timeout"


# --- API -----------------------------------------------------------------------------------


async def _seed(org_id, user_id, device_id, content, when):
    async with async_session_factory() as db:
        job = CollectionJob(org_id=org_id, created_by_id=user_id, status=JobStatus.COMPLETED)
        db.add(job)
        await db.flush()
        item = CollectionJobItem(job_id=job.id, device_id=device_id, status=JobStatus.COMPLETED)
        db.add(item)
        await db.flush()
        db.add(ConfigSnapshot(device_id=device_id, job_item_id=item.id, content=content, collected_at=when))
        await db.commit()


@pytest.mark.asyncio
async def test_lookup_and_partial_snapshots(client: AsyncClient, unique_email):
    resp = await client.post("/api/auth/register", json={"email": unique_email, "password": "supersecret1", "org_name": "Clients Org"})
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=headers)).json()
    dev = await client.post("/api/devices", json={"name": "HQ-WLC01", "host": "10.0.0.5", "device_type": "cisco_wlc_9800"}, headers=headers)
    device_id = dev.json()["id"]
    now = datetime.now(timezone.utc)
    full = snapshot({"show running-config": "hostname HQ-WLC01\n", "show ap summary": "", "show logging": XE_LOG})
    partial = snapshot({"show wireless client mac-address 1111.2222.3333 mobility history": MOBILITY, "show logging | include 1111.2222.3333": XE_LOG.splitlines()[3]})
    await _seed(me["org_id"], me["id"], device_id, full, now - timedelta(hours=2))
    await _seed(me["org_id"], me["id"], device_id, partial, now - timedelta(minutes=5))

    # The audit uses the full collection, not the newer client investigation.
    report = (await client.get("/api/wireless-audit?dns=false", headers=headers)).json()
    ctl = report["controllers"][0]
    assert not any(c["check_id"] == "data.commands" and "show running-config" in (c["note"] or "") for c in ctl["checks"])
    assert any(e["mac"] == "4444.5555.6666" for e in report["client_events"])

    resp = await client.get("/api/wireless-audit/clients/11:11:22:22:33:33", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mac"] == "1111.2222.3333" and body["snapshots_searched"] == 2
    h = body["histories"][0]
    assert h["controller_name"] == "HQ-WLC01" and h["drops"] == 1 and len(h["roam_history"]) == 3
    assert len([e for e in h["events"] if e["kind"] == "drop"]) == 1  # the same line in both snapshots counted once
    assert body["commands"]["cisco_wlc_9800"][2] == "show logging | include 1111.2222.3333"
    assert body["commands"]["cisco_wlc"][0] == "show client detail 11:11:22:22:33:33"
    assert (await client.get("/api/wireless-audit/clients/not-a-mac", headers=headers)).status_code == 400
