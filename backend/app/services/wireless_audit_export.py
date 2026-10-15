"""The Wireless Audit as an Excel workbook: a summary per controller, then
checks, findings, access points, clients and logs, one sheet each."""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from app.services.inventory_export import _HEADER_FILL, _HEADER_FONT, _fmt_dt, _write_table, _yes_no

_SEVERITY_FILL = {
    "fail": PatternFill("solid", fgColor="FDE2E1"),
    "warn": PatternFill("solid", fgColor="FEF3C7"),
    "pass": PatternFill("solid", fgColor="DCFCE7"),
}


def _shade(ws, column: int) -> None:
    """Colours each row by the severity/status word in `column` (1-based)."""
    for row in ws.iter_rows(min_row=2):
        fill = _SEVERITY_FILL.get(str(row[column - 1].value).lower())
        if fill:
            row[column - 1].fill = fill


def build_audit_workbook(report: dict, org_name: str) -> bytes:
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    s = report["summary"]
    rows = [
        ["Organization", org_name],
        ["Generated", _fmt_dt(report["generated_at"])],
        ["DNS checked", "Yes" if report["dns_checked"] else "No"],
        ["Controllers", s["controllers"]],
        ["Access points", s["access_points"]],
        ["  with issues", s["aps_with_issues"]],
        ["Wireless clients", s["clients"]],
        ["  with poor signal", s["clients_poor"]],
        ["Failures", s["fail"]],
        ["Warnings", s["warn"]],
        ["Notes", s["info"]],
        [],
        ["Controller", "Platform", "Host", "Model", "Software", "Collected", "APs", "Clients", "Score", "Failed checks", "Warning checks", "Standard profile"],
    ]
    for row in rows:
        summary.append(row)
    header_row = len(rows)
    for c in report["controllers"]:
        summary.append(
            [
                c["name"],
                "Catalyst 9800" if c["platform"] == "9800" else "AireOS",
                c["host"],
                c["model"],
                c["software"],
                _fmt_dt(c["collected_at"]) if c["collected_at"] else "never",
                c["ap_count"],
                c["client_count"],
                "" if c["score"] is None else f"{c['score']}%",
                c["counts"].get("fail", 0),
                c["counts"].get("warn", 0),
                c.get("template_profile"),
            ]
        )
    summary["A1"].font = Font(bold=True)
    for cell in summary[header_row]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
    for col, width in zip("ABCDEFGHIJKL", (28, 14, 18, 18, 14, 20, 8, 8, 8, 14, 14, 28)):
        summary.column_dimensions[col].width = width

    ws = wb.create_sheet("Checks")
    _write_table(
        ws,
        ["Controller", "Category", "Check", "Status", "Evaluated", "Issues", "Note"],
        [[c["name"], ch["category"], ch["title"], ch["status"].upper(), ch["evaluated"], ch["issues"], ch["note"]] for c in report["controllers"] for ch in c["checks"]],
    )
    _shade(ws, 4)

    ws = wb.create_sheet("Findings")
    _write_table(
        ws,
        ["Severity", "Controller", "Category", "Subject", "Finding", "Expected", "Actual", "Check"],
        [[f["severity"].upper(), f["controller_name"], f["category"], f["subject"], f["message"], f["expected"], f["actual"], f["check_id"]] for f in report["findings"]],
    )
    _shade(ws, 1)

    _write_table(
        wb.create_sheet("Access points"),
        ["Controller", "AP name", "Model", "Serial", "MAC", "IP", "State", "Country", "Software", "Clients", "AP group", "Site tag", "Policy tag", "RF tag", "Primary WLC", "Uplink", "Radios", "Name OK", "In AP subnet", "DNS name", "DNS address(es)", "DNS OK", "PTR", "PTR OK", "Issues"],
        [
            [
                a["controller_name"], a["name"], a["model"], a["serial"], a["mac"], a["ip"], a["state"], a["country"], a["software"], a["clients"],
                a.get("ap_group"), a["site_tag"], a["policy_tag"], a["rf_tag"], a["primary_controller"], a["uplink"], a["radios"],
                _yes_no(a["name_ok"]), _yes_no(a["subnet_ok"]), a["fqdn"], ", ".join(a["dns_ips"]), _yes_no(a["dns_ok"]), a["ptr"], _yes_no(a["ptr_ok"]), a["issues"],
            ]
            for a in report["access_points"]
        ],
    )

    _write_table(
        wb.create_sheet("Clients"),
        ["Controller", "MAC", "Manufacturer", "IP", "Username", "AP", "SSID", "State", "Protocol", "RSSI (dBm)", "SNR (dB)", "Health", "Issues"],
        [
            [c["controller_name"], c["mac"], c["manufacturer"], c["ip"], c["username"], c["ap_name"], c["ssid"], c["state"], c["protocol"], c["rssi"], c["snr"], c["health"], "; ".join(c["issues"])]
            for c in report["clients"]
        ],
    )

    _write_table(
        wb.create_sheet("Client health"),
        ["Controller", "Clients", "With RF detail", "Good", "Fair", "Poor", "Avg RSSI", "Avg SNR", "2.4 GHz", "5 GHz", "6 GHz", "Not in RUN", "No IP", "Legacy"],
        [
            [h["controller_name"], h["total"], h["with_rf_detail"], h["good"], h["fair"], h["poor"], h["avg_rssi"], h["avg_snr"], h["band_24"], h["band_5"], h["band_6"], h["not_run"], h["no_ip"], h["legacy"]]
            for h in report["client_health"]
        ],
    )

    _write_table(
        wb.create_sheet("AP groups & tags"),
        ["Controller", "Kind", "Name", "Description", "APs", "Models", "WLANs", "Profiles", "WNCD", "Issues"],
        [[g["controller_name"], g["kind"], g["name"], g["description"], g["ap_count"], g["models"], g["wlans"], g["profiles"], g["wncd"], g["issues"]] for g in report.get("groups", [])],
    )

    _write_table(
        wb.create_sheet("WNCD"),
        ["Controller", "WNCD instance", "Site tags", "APs", "CPU 5 min %"],
        [[w["controller_name"], w["instance"], ", ".join(w["site_tags"]), w["aps"], w["cpu_5min"]] for w in report.get("wncd", [])],
    )

    if report.get("r2o"):
        ws = wb.create_sheet("Return to Operations")
        _write_table(
            ws,
            ["Status", "Controller", "Checklist", "Row", "Check", "Stage", "Level", "Command(s)", "Result", "How to fix"],
            [
                [r["status"].upper(), r["controller_name"], r["checklist"], r["row"], r["description"], r["stage"], r["level"], "; ".join(r["commands"]), "\n".join(r["details"]), "\n".join(r["fix"])]
                for r in report["r2o"]
            ],
        )
        _shade(ws, 1)

    _write_table(
        wb.create_sheet("Logs"),
        ["Controller", "Source", "Message", "Severity", "Count", "Notable", "Latest example"],
        [[g["controller_name"], g["source"], g["key"], g["severity"], g["count"], "Yes" if g["notable"] else "", g["example"]] for g in report["logs"]],
    )

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
