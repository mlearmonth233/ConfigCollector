"""Importing devices from an IT/OT Sitebook's "Devices and Serials" tab."""

from io import BytesIO

import pytest
from httpx import AsyncClient
from openpyxl import Workbook

from app.services.sitebook import SitebookError, is_ot, is_private_ip, parse_sitebook, type_from_pid

HEADER = ["Host", "Location", "IP", "PID", "SN", "Mac Address", "Asset Tag", "Site Provided Location (To be used in ALL documentation going forward)", "Rack El Picture available"]
ROWS = [
    ["SITEA-OT-AREA1-SW01-L", "Area1, Shop Floor", "10.20.30.11", "1783-BMS10CGA", "FDO0000AAA1", "5c:88:16:00:00:01", None, "Area1, Shop Floor", "No"],
    ["SITEA-OT-LINE1-SW01-U", "Line 1", "10.20.30.12", "1783-MMS10", "FCW0000AAA2", None, None, "Line 1", "No"],
    ["SITEA-OT-LINE1-SW01-U-2", None, "10.20.30.12", "1783-MMX16T", "FCW0000AAA3", None, None, "Site to provide", "No"],
    ["SITEA-OT-BMS-SW01-U", "Plant room", "10.20.30.13", "1783-CMS20DN", "SV4000001", None, None, "Site to provide", "No"],
    ["SITEA01FWL001", "MDF1 Rack 5", "10.20.0.24", "FortiGate-200G", "FG2H0000000001", None, None, "MDF1 Rack 5", "No"],
    ["SITEA01SWA001", "MDF1 Rack 6", "10.20.1.11", "C9300-48U", "FJC0000AAA4", None, None, "MDF1 Rack 6", "No"],
    ["SITEA01SWA001-2", "MDF1 Rack 6", "10.20.1.11", "C9300-48U", "FJC0000AAA5", None, None, "MDF1 Rack 6", "No"],
    ["SITEA01SWA001-3", "MDF1 Rack 6", "10.20.1.11", "C9300-48U", "FJC0000AAA6", None, None, "MDF1 Rack 6", "No"],
    ["SITEA01WLC001", "MDF1 Rack 4", "10.20.1.5", "C9800-40-K9", "TTM0000AAA7", None, None, "MDF1 Rack 4", "No"],
    ["SITEA01SWA009", "IDF-9", "TBC", "C9300-24UX", "FJC0000AAA8", None, None, "IDF-9", "No"],
    [None, None, None, None, None, None, None, None, None],
]


def sitebook_bytes(sheet: str = "Devices and Serials", extra_sheets: bool = True) -> bytes:
    wb = Workbook()
    if extra_sheets:
        wb.active.title = "Summary"
        ws = wb.create_sheet(sheet)
    else:
        ws = wb.active
        ws.title = sheet
    ws.append(HEADER)
    for row in ROWS:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_keeps_one_device_per_ip():
    book = parse_sitebook(sitebook_bytes())
    assert book.sheet == "Devices and Serials"
    names = [d.name for d in book.devices]
    assert names == ["SITEA-OT-AREA1-SW01-L", "SITEA-OT-LINE1-SW01-U", "SITEA-OT-BMS-SW01-U", "SITEA01FWL001", "SITEA01SWA001", "SITEA01WLC001"]
    stack = next(d for d in book.devices if d.name == "SITEA01SWA001")
    assert stack.host == "10.20.1.11" and stack.stack_members == ["SITEA01SWA001-2", "SITEA01SWA001-3"]
    assert stack.location == "MDF1 Rack 6" and stack.pid == "C9300-48U" and stack.serial == "FJC0000AAA4"
    # "Site to provide" falls back to the Location column.
    assert next(d for d in book.devices if d.name == "SITEA-OT-BMS-SW01-U").location == "Plant room"
    reasons = {s.name: s.reason for s in book.skipped}
    assert reasons["SITEA-OT-LINE1-SW01-U-2"] == "same IP as SITEA-OT-LINE1-SW01-U (stack member)"
    assert reasons["SITEA01SWA009"] == "no valid IP address in column C"


def test_parse_finds_the_tab_and_rejects_bad_files():
    assert parse_sitebook(sitebook_bytes("devices & serials", extra_sheets=False)).devices
    with pytest.raises(SitebookError, match="Devices and Serials"):
        parse_sitebook(sitebook_bytes("Inventory"))
    with pytest.raises(SitebookError):
        parse_sitebook(b"not a workbook")


def test_type_from_pid():
    assert type_from_pid("C9300-48U") == ("cisco_ios", None)
    assert type_from_pid("1783-BMS10CGA") == ("cisco_ios", None)
    assert type_from_pid("FortiGate-200G") == ("fortinet", "firewall")
    assert type_from_pid("C9800-40-K9") == ("cisco_wlc_9800", "wlc")
    assert type_from_pid("AIR-CT5520-K9") == ("cisco_wlc", "wlc")
    assert type_from_pid("AP8853") == ("apc_pdu", "pdu")
    assert type_from_pid(None) == (None, None)


@pytest.mark.asyncio
async def test_sitebook_api(client: AsyncClient, unique_email):
    resp = await client.post("/api/auth/register", json={"email": unique_email, "password": "supersecret1", "org_name": "Sitebook Org"})
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    resp = await client.post("/api/devices", json={"name": "SITEA01FWL001", "host": "10.20.0.24", "device_type": "fortinet"}, headers=headers)
    assert resp.status_code == 201, resp.text

    files = {"file": ("sitebook.xlsx", sitebook_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    resp = await client.post("/api/devices/import/sitebook", files=files, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    devices = {d["name"]: d for d in body["devices"]}
    assert len(devices) == 6 and len(body["skipped"]) == 4
    assert devices["SITEA01FWL001"]["existing_device_id"] is not None
    assert devices["SITEA01SWA001"]["existing_device_id"] is None
    assert devices["SITEA01WLC001"]["suggested_device_type"] == "cisco_wlc_9800"
    assert devices["SITEA-OT-LINE1-SW01-U"]["suggested_device_type"] == "cisco_ios"
    assert devices["SITEA-OT-LINE1-SW01-U"]["ot"] is True and devices["SITEA-OT-LINE1-SW01-U"]["private_ip"] is True
    assert devices["SITEA01SWA001"]["ot"] is False
    # Nothing was saved by reading it.
    assert len((await client.get("/api/devices", headers=headers)).json()) == 1

    resp = await client.post("/api/devices/import/sitebook", files={"file": ("x.xlsx", b"nope", "application/octet-stream")}, headers=headers)
    assert resp.status_code == 400


def test_private_ip_and_ot():
    assert is_private_ip("10.3.100.12") and is_private_ip("172.20.1.1") and is_private_ip("192.168.0.5") and is_private_ip("100.64.1.1")
    assert not is_private_ip("137.180.52.87") and not is_private_ip("172.32.0.1") and not is_private_ip("wlc01.corp")
    assert is_ot("1783-BMS10CGA") and is_ot("IE-3400-8T2S") and not is_ot("C9300-48U") and not is_ot(None)
