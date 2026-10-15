"""Return to Operations: importing the checklist workbook, reading each
row's criteria into checks, evaluating them against a snapshot, the
collector's {site}/{ap} expansion, and the API."""

from io import BytesIO

import pytest
from httpx import AsyncClient
from openpyxl import Workbook

from app.schemas.wireless_audit import R2OChecklist, R2OItemModel, WirelessStandard
from app.services import collector
from app.services import inventory as inv
from app.services import r2o
from app.services import wireless_audit as wa

HEADER = ["Requested By", "Description", "What and how to get the relevant data", "Expected value - What it should be?", "Validation Criteria for Pass/Fail", "R2O Automation", "Comments / Remmarks", "Pre or Post Cutover", "Mandatory or Gold Config"]

AIREOS_ROWS = [
    ["eng", "Validate only approved users are configured", "show mgmtuser", "", "Only those 2 accounts (local and ladmin) should be configured", "", "", "POST", "M"],
    ["eng", "Syslog configuration", 'grep include Host "show logging"', "  - Host 0..... 10.0.0.1\n  - Host 1..... 10.0.0.2", "Only those 2 destination IP should be configured", "", "", "PRE", "M"],
    ["eng", "Netflow configuration", "show flow exporter summary", "", "Exporter IP must match depending on region:\nNA: 10.1.0.1 | EU: 10.2.0.1", "", "", "PRE", "M"],
    ["eng", "Device clock is synchronized", "show time", "", 'Status should be "In Sync". If more than 1 row returned, just check that at least 1 Status says "In Sync"', "", "", "PRE", "M"],
    ["eng", "Proper code version installed on WLC", "show sysinfo", "", "Product Version.................................. 8.10.196.0\n", "", "", "PRE", "M"],
    ["eng", "RF Group name is proper", "show advanced 802.11a group", "", "  RF Group Name.................................. ABCDEF01\nMust match the first 8 characters of device name", "", "", "PRE", "M"],
    ["eng", "Priority order for management users (config aaa auth mgmt local tacacs)", "show aaa auth", "", 'Order must be "local" then "tacacs"', "", "", "PRE", "M"],
    ["eng", "SNMP v1 and v2 are disabled", "show snmpversion", "", 'v1 and v2c must be "Disable"\nv3 must be "Enable"', "", "", "PRE", "M"],
    ["eng", "SNMP communities public and private are deleted", "show snmpcommunity", "", 'Words "public" and "private" not found in output', "", "", "PRE", "M"],
    ["eng", "HTTP and telnet is disabled for MGMT", "show network summary", "", 'Web Mode must be "Disable"\nTelnet must be "Disable"', "", "", "PRE", "M"],
    ["eng", "cwa_redirect ACL is configured", "show acl summary", "", 'cwa_redirect must be present and must be "Yes" under "Applied" column', "", "", "PRE", "M"],
    ["eng", "TPC and DCA for 5 GHz radios", "show advanced 802.11a channel", "", 'Channel Update Interval........................ "12 Hours"', "", "", "PRE", "G"],
    ["eng", "Check RF-profile settings", "show rf-profile details 2g-group01", "", "Min Transmit Power should be 3 dBm\nMax Transmit Power should be 9 dBm", "", "", "PRE", "M"],
    ["eng", "Validation of main controller being active", "show redundancy summary", "", "For 2 WLC\nRedundancy Mode = SSO ENABLED\nPeer State = STANDBY HOT", "", "Only if 2 WLC are installed.", "PRE", "M"],
    ["eng", "AP group check", "show wlan apgroups", "", "PASS There should be no AP under: Site Name.... default-group FAIL: If there is", "", "", "PRE", None],
    ["eng", "Quantity of registered AP in WLC", "show ap summary", "Number of APs.... 2", "Number should match documentation", "", "", "PRE", "M"],
    ["eng", "Something only a person can judge", "show rf-profile summary", "", "For office sites it depends on the business.", "", "", "PRE", None],
    ["eng", "Not collected yet", "show media-stream group summary", "", 'Status must be "Multicast-direct"', "", "", "PRE", "M"],
]

XE_ROWS = [
    ["eng", "Wireless Tag RF Validation", "show wireless tag rf detailed XXXXXXX-APGROUP01", "WLC#show wireless tag rf detailed ABCDEF01-APGROUP01\n5ghz RF Policy : 5g-ABCDEF01-group01\nLast Changed : 10 days ago", "", "", "", "", ""],
    ["eng", "Verify SSH for VTY lines", '"show run | s line vty"', "", '"transport input ssh" must be present in all VTY line sections. This section must not contain "telnet" or "transport input all".', "", "", "", ""],
    ["eng", "Optimized Roaming is disabled", "show running-config | in optimize", "", "PASS:  (no output of optimize roaming config).\n\nFAIL If there is an output of:\nap dot11 5ghz rrm optimized-roam", "", "", "", ""],
    ["eng", "Verify NTP configuration", '"show run | i ntp"', "ntp source Vlan102\nntp server 10.0.0.123", "Result must be as per expected value.", "", "", "", ""],
    ["eng", "AP speed", '"show ap summary" , "sh ap name <APNAME> ethernet statistics"', "", "PASS: GigabitEthernet0 should be UP Speed should be either 5000, 2500, 1000", "", "", "", ""],
]


def checklist_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "WLC AireOS"
    ws.append([None, None, None, None, None, None, None, "B A C K"])
    ws.append([])
    ws.append([None, *HEADER])
    for row in AIREOS_ROWS:
        ws.append([None, *row])
    xe = wb.create_sheet("Flex WLC 9800")
    xe.append([None, None, None, None, None, None, "B A C K"])
    xe.append([])
    xe.append(HEADER)
    for row in XE_ROWS:
        xe.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def items(sheet: str) -> dict[str, r2o.R2OItem]:
    sheets = {s.name: s for s in r2o.import_checklist(checklist_bytes())}
    return {i.description: i for i in sheets[sheet].items}


AIREOS_OUTPUT = {
    "show mgmtuser": "User Name  Permissions  Description  Password Strength  Telnet Capable\n------- -------\nlocal  read-write  Strong  Yes\nladmin read-write Strong Yes\nintruder read-write Strong Yes\n",
    'grep include Host "show logging"': "  - Host 0....................................... 10.0.0.1\n  - Host 1....................................... 10.9.9.9\n",
    "show flow exporter summary": "Exporter Name  IP  Port\nexp1  10.2.0.1  2055\n",
    "show time": "NTP Servers\n   1   0   192.168.16.1   In Sync   AUTH DISABLED\n",
    "show sysinfo": "Product Version.................................. 8.10.185.0\n",
    "show advanced 802.11a group": "  RF Group Name.................................. ABCDEF01\n",
    "show aaa auth": "Management authentication server order:\n    1............................................ tacacs\n    2............................................ local\n",
    "show snmpversion": "SNMP v1 Mode.................................... Disable\nSNMP v2c Mode.................................... Enable\nSNMP v3  Mode.................................... Enable\n",
    "show snmpcommunity": "SNMP Community Name  Client IP Address\nprivate   0.0.0.0\n",
    "show network summary": "Web Mode.................................... Disable\nTelnet...................................... Enable\n",
    "show acl summary": "IPv4 ACL Name   Applied\n-------- -------\ncwa_redirect     Yes\n",
    "show advanced 802.11a channel": "  Channel Update Interval........................ 12 Hours\n",
    "show rf-profile details 2g-group01": "Min Transmit Power............................. 3 dBm\nMax Transmit Power............................. 12 dBm\n",
    "show redundancy summary": "Redundancy Mode = SSO DISABLED\n     Local State = ACTIVE\n      Peer State = N/A\n",
    "show wlan apgroups": "Site Name.... default-group\n",
    "show ap summary": "Number of APs.................................... 2\n",
    "show rf-profile summary": "Number of RF-profiles: 2\n",
}


def snapshot(outputs: dict[str, str]) -> str:
    return "\n".join(f"! ---- {k} ----\n{v}" for k, v in outputs.items())


def run_aireos() -> dict[str, r2o.R2OResult]:
    content = snapshot(AIREOS_OUTPUT)
    found = {"grp.default": [{"subject": "default-group", "message": "2 AP(s) in default-group"}]}
    return {
        desc: r2o.evaluate(item, content, "ABCDEF01WLC001", [], found, lambda it: r2o.fix_hints(it, "aireos"))
        for desc, item in items("WLC AireOS").items()
    }


def test_import_reads_both_layouts():
    sheets = {s.name: s for s in r2o.import_checklist(checklist_bytes())}
    assert (sheets["WLC AireOS"].platform, sheets["Flex WLC 9800"].platform, sheets["Flex WLC 9800"].kind) == ("aireos", "9800", "flex")
    ae = {i.description: i for i in sheets["WLC AireOS"].items}
    assert ae["Validate only approved users are configured"].stage == "POST"
    assert ae["TPC and DCA for 5 GHz radios"].level == "G"
    xe = {i.description: i for i in sheets["Flex WLC 9800"].items}
    assert xe["Wireless Tag RF Validation"].commands == ["show wireless tag rf detailed {site}-APGROUP01"]
    assert xe["Verify SSH for VTY lines"].commands == ["show run | s line vty"]
    assert xe["AP speed"].commands == ["show ap summary", "sh ap name {ap} ethernet statistics"]
    assert ae["Syslog configuration"].commands == ['grep include Host "show logging"']


def test_aireos_evaluation():
    res = run_aireos()
    status = {k: v.status for k, v in res.items()}
    assert status["Validate only approved users are configured"] == "fail"
    assert "intruder" in res["Validate only approved users are configured"].details[0]
    assert status["Syslog configuration"] == "fail"
    assert "10.9.9.9" in res["Syslog configuration"].details[0] and "10.0.0.2" in res["Syslog configuration"].details[0]
    assert status["Netflow configuration"] == "pass"  # one of the regional IPs
    assert status["Device clock is synchronized"] == "pass"
    assert status["Proper code version installed on WLC"] == "fail"
    assert status["RF Group name is proper"] == "pass"
    assert status["Priority order for management users (config aaa auth mgmt local tacacs)"] == "fail"
    assert "config aaa auth mgmt local tacacs" in res["Priority order for management users (config aaa auth mgmt local tacacs)"].fix
    assert status["SNMP v1 and v2 are disabled"] == "fail"
    assert res["SNMP v1 and v2 are disabled"].details == ["v2c is 'Enable', expected 'Disable'"]
    assert status["SNMP communities public and private are deleted"] == "fail"
    assert status["HTTP and telnet is disabled for MGMT"] == "fail"
    assert any("config network telnet disable" in f for f in res["HTTP and telnet is disabled for MGMT"].fix)
    assert status["cwa_redirect ACL is configured"] == "pass"
    assert status["TPC and DCA for 5 GHz radios"] == "pass"
    assert status["Check RF-profile settings"] == "fail"
    assert res["Check RF-profile settings"].details == ["Max Transmit Power is '12 dBm', expected '9 dBm'"]
    assert status["Validation of main controller being active"] == "na"
    assert status["AP group check"] == "fail"  # from the audit's default-group finding
    assert status["Quantity of registered AP in WLC"] == "manual"
    assert "2 APs registered" in res["Quantity of registered AP in WLC"].details[0]
    assert status["Something only a person can judge"] == "manual"
    assert status["Not collected yet"] == "no_data"


def test_gold_level_is_a_warning():
    content = snapshot({"show advanced 802.11a channel": "  Channel Update Interval........................ 600 seconds\n"})
    item = items("WLC AireOS")["TPC and DCA for 5 GHz radios"]
    assert r2o.evaluate(item, content, "X", [], {}, lambda it: []).status == "warn"


def test_xe_evaluation_with_site_code_and_blocks():
    xe = items("Flex WLC 9800")
    content = snapshot(
        {
            "show wireless tag rf detailed QRSTUV02-APGROUP01": "5ghz RF Policy : 5g-QRSTUV02-group01\nLast Changed : 3 days ago\n",
            "show run | s line vty": "line vty 0 4\n transport input ssh\nline vty 5 15\n transport input telnet ssh\n",
            "show running-config | in optimize": "ap dot11 5ghz rrm optimized-roam\n",
            "show run | i ntp": "ntp source Vlan102\nntp server 10.0.0.123\n",
        }
    )
    ev = lambda d: r2o.evaluate(xe[d], content, "QRSTUV02WLC001", [], {"ap.uplink": []}, lambda it: [])
    # The checklist's example site code is swapped for this controller's.
    assert ev("Wireless Tag RF Validation").status == "pass"
    vty = ev("Verify SSH for VTY lines")
    assert vty.status == "fail" and vty.details == ["Must not be present, but found: telnet"]
    roam = ev("Optimized Roaming is disabled")
    assert roam.status == "fail" and any("optimized-roam" in d for d in roam.details)
    assert ev("Verify NTP configuration").status == "pass"
    assert ev("AP speed").status == "pass"  # no AP uplink findings


def test_expand_and_site_code():
    assert r2o.expand("show wireless tag site detailed {site}-APGROUP01", "SITE0001WLC001") == ["show wireless tag site detailed SITE0001-APGROUP01"]
    assert r2o.expand("sh ap name {ap} ethernet statistics", "X", ["AP1", "AP2"]) == ["sh ap name AP1 ethernet statistics", "sh ap name AP2 ethernet statistics"]


def test_collector_expands_site_and_ap(monkeypatch):
    class FakeConn:
        def __init__(self):
            self.sent = []

        def send_command_timing(self, command, **kwargs):
            self.sent.append(command)
            if command == "show ap summary":
                return "AP Name  Slots  AP Model  Ethernet MAC\nAP-ONE  2  C9130AXI-B  a4b2.3c4d.5e60\nAP-TWO  2  C9130AXI-B  a4b2.3c4d.5e61\n"
            return "ok"

    conn = FakeConn()

    from contextlib import contextmanager

    @contextmanager
    def fake_session(**kwargs):
        yield conn

    monkeypatch.setattr(collector, "open_device_session", fake_session)
    out = collector.collect_device_config(
        host="h", port=22, device_type="cisco_wlc_9800", username="u", password="p", secret=None, custom_commands=None, auth_timeout=10,
        commands_override=["show wireless tag site detailed {site}-APGROUP01", "show ap summary", "sh ap name {ap} ethernet statistics"],
        device_name="SITE0001WLC001",
    )
    assert conn.sent == ["show wireless tag site detailed SITE0001-APGROUP01", "show ap summary", "sh ap name AP-ONE ethernet statistics", "sh ap name AP-TWO ethernet statistics"]
    assert "! ---- sh ap name AP-TWO ethernet statistics ----" in out


def test_continue_prompts_are_answered():
    class Conn:
        def __init__(self):
            self.answers = []

        def send_command_timing(self, answer, **kwargs):
            self.answers.append(answer)
            return "rest of the output\n(Cisco Controller) >"

    conn = Conn()
    assert collector._answer_continue_prompts(conn, "first part\nPress Enter to continue...") == "first part\nrest of the output\n(Cisco Controller) >"
    assert conn.answers == ["\n"]


def test_audit_runs_the_assigned_checklist():
    sheets = r2o.import_checklist(checklist_bytes())
    lists = [
        R2OChecklist(
            name=s.name, platform=s.platform, kind=s.kind,
            items=[R2OItemModel(item_id=i.item_id, row=i.row, description=i.description, commands=i.commands, expected=i.expected, criteria=i.criteria, stage=i.stage, level=i.level) for i in s.items],
        )
        for s in sheets
    ]
    lists[0].items[1].enabled = False  # syslog row switched off
    std = WirelessStandard(r2o_checklists=lists)
    inputs = [inv.SnapshotInput("ae", "ABCDEF01WLC001", "10.0.0.9", "cisco_wlc", None, None, snapshot(AIREOS_OUTPUT), None, "s")]
    report = wa.run_audit(wa.gather(inputs), std, None)
    rows = report["r2o"]
    assert {r["checklist"] for r in rows} == {"WLC AireOS"}
    assert "Syslog configuration" not in {r["description"] for r in rows}
    ctl = report["controllers"][0]
    assert ctl["r2o_checklist"] == "WLC AireOS" and ctl["r2o_counts"]["fail"] >= 5
    assert wa.r2o_commands(std, "9800") == ["show wireless tag rf detailed {site}-APGROUP01", "show run | s line vty", "show running-config | in optimize", "show run | i ntp", "show ap summary", "sh ap name {ap} ethernet statistics"]


async def _register(client: AsyncClient, email: str) -> dict:
    resp = await client.post("/api/auth/register", json={"email": email, "password": "supersecret1", "org_name": "R2O Org"})
    assert resp.status_code in (200, 201), resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_r2o_api(client: AsyncClient, unique_email):
    headers = await _register(client, unique_email)
    resp = await client.post("/api/wireless-audit/r2o/import", files={"file": ("r2o.xlsx", checklist_bytes(), "application/octet-stream")}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert {c["name"] for c in resp.json()["checklists"]} == {"WLC AireOS", "Flex WLC 9800"}

    resp = await client.get("/api/wireless-audit/r2o/commands", headers=headers)
    assert resp.status_code == 200
    by_type = {c["device_type"]: c for c in resp.json()}
    assert "show mgmtuser" in by_type["cisco_wlc"]["missing"]
    resp = await client.post("/api/wireless-audit/r2o/commands/cisco_wlc", headers=headers)
    assert resp.status_code == 200
    assert "show mgmtuser" in resp.json()["commands"]
    resp = await client.get("/api/wireless-audit/r2o/commands", headers=headers)
    assert {c["device_type"]: c for c in resp.json()}["cisco_wlc"]["missing"] == []

    resp = await client.get("/api/wireless-audit/standard", headers=headers)
    assert len(resp.json()["standard"]["r2o_import_history"]) == 1
    resp = await client.post("/api/wireless-audit/r2o/import", files={"file": ("bad.xlsx", b"nope", "application/octet-stream")}, headers=headers)
    assert resp.status_code == 400
