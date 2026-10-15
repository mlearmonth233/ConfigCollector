"""Wireless Audit: every 9800 / AireOS controller checked against the
organization's wireless standard, from the latest collected snapshots,
with live DNS lookups for AP names and addresses."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.command_profiles import get_org_command_overrides
from app.api.deps import get_current_user, require_admin
from app.api.inventory import _covered, _inputs
from app.database import get_db
from app.models.command_profile import CommandProfile
from app.models.device import Device
from app.models.organization import Organization
from app.models.snapshot import ConfigSnapshot
from app.models.user import User
from app.models.wireless_audit import WirelessStandardRecord
from app.schemas.command_profile import CommandProfileOut
from app.schemas.wireless_audit import (
    AuditCommandCoverageOut,
    ClientLookupOut,
    ImportRecord,
    R2OChecklist,
    R2OImportOut,
    R2OItemModel,
    SkippedLineOut,
    TemplateProfile,
    WirelessAuditOut,
    WirelessStandard,
    WirelessStandardOut,
    WorkbookImportOut,
)
from app.services import wireless_audit as wa
from app.services import client_events as ce
from app.services import inventory as inv
from app.services import r2o as r2o_engine
from app.services.standard_workbook import import_workbook
from app.services.device_types import DEVICE_TYPE_REGISTRY, parse_command_list
from app.services.wireless_audit_export import build_audit_workbook

router = APIRouter(prefix="/api/wireless-audit", tags=["wireless-audit"])
log = logging.getLogger(__name__)

DNS_CONCURRENCY = 32
DNS_TIMEOUT_SECONDS = 4.0
# The whole DNS step: what is not looked up by then is reported "not
# checked" rather than holding the audit up.
DNS_BUDGET_SECONDS = 60.0
MAX_WORKBOOK_BYTES = 25 * 1024 * 1024
MAX_IMPORT_HISTORY = 25


async def _load_standard(db: AsyncSession, org_id) -> tuple[WirelessStandard, WirelessStandardRecord | None]:
    row = await db.scalar(select(WirelessStandardRecord).where(WirelessStandardRecord.org_id == org_id))
    if row is None:
        return WirelessStandard(), None
    try:
        return WirelessStandard.model_validate(json.loads(row.settings)), row
    except (ValueError, TypeError):
        return WirelessStandard(), row


def _validate(standard: WirelessStandard) -> None:
    problems = []
    if standard.ap_name_pattern.strip():
        try:
            re.compile(standard.ap_name_pattern.strip())
        except re.error as exc:
            problems.append(f"AP naming pattern: {exc}")
    for cidr in standard.ap_subnets:
        try:
            ipaddress.ip_network(cidr.strip(), strict=False)
        except ValueError:
            problems.append(f"'{cidr}' is not a subnet (use CIDR, e.g. 10.20.0.0/22)")
    for pattern in standard.log_ignore_patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            problems.append(f"Log ignore pattern '{pattern}': {exc}")
    if standard.ap_name_case not in ("any", "upper", "lower"):
        problems.append("AP name case must be any, upper or lower")
    if problems:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="; ".join(problems))


@router.get("/standard", response_model=WirelessStandardOut)
async def get_standard(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> WirelessStandardOut:
    standard, row = await _load_standard(db, user.org_id)
    return WirelessStandardOut(standard=standard, updated_at=(row.updated_at or row.created_at) if row else None, is_default=row is None)


@router.put("/standard", response_model=WirelessStandardOut)
async def save_standard(payload: WirelessStandard, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> WirelessStandardOut:
    _validate(payload)
    _, row = await _load_standard(db, admin.org_id)
    settings = payload.model_dump_json()
    if row is None:
        row = WirelessStandardRecord(org_id=admin.org_id, settings=settings, updated_by_id=admin.id)
        db.add(row)
    else:
        row.settings = settings
        row.updated_by_id = admin.id
    await db.commit()
    return await get_standard(admin, db)


@router.post("/standard/import", response_model=WorkbookImportOut)
async def import_standard_workbook(
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> WorkbookImportOut:
    """Reads the configuration-standard workbook (one sheet per controller
    build) into template profiles. A sheet replaces the profile of the same
    name; other profiles and every other setting of the standard are kept,
    so re-importing an updated workbook is all it takes to move to it."""
    content = await file.read(MAX_WORKBOOK_BYTES + 1)
    if len(content) > MAX_WORKBOOK_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The workbook is larger than 25 MB")
    filename = file.filename or "standard.xlsx"
    try:
        imported = await asyncio.to_thread(import_workbook, content, filename)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types for a bad file
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not read {filename} as an Excel workbook: {exc}") from exc
    if not imported.sheets:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No sheet in the workbook has controller commands in column A")

    standard, row = await _load_standard(db, admin.org_id)
    now = datetime.now(timezone.utc)
    profiles = [
        TemplateProfile(
            name=sheet.name,
            platform=sheet.platform,
            kind=sheet.kind,
            template=sheet.template,
            source=filename,
            imported_at=now,
            rules=sheet.rules,
            optional=sheet.optional,
            skipped_count=sheet.skipped_count,
            skipped=[SkippedLineOut(row=x.row, text=x.text, reason=x.reason) for x in sheet.skipped],
        )
        for sheet in imported.sheets
    ]
    new_names = {p.name for p in profiles}
    replaced = [p.name for p in standard.template_profiles if p.name in new_names]
    standard.template_profiles = [p for p in standard.template_profiles if p.name not in new_names] + profiles
    record = ImportRecord(filename=filename, sha256=imported.sha256, imported_at=now, imported_by=admin.email, sheets=[p.name for p in profiles], rules=sum(p.rules for p in profiles))
    standard.import_history = [record, *standard.import_history][:MAX_IMPORT_HISTORY]
    settings = standard.model_dump_json()
    if row is None:
        db.add(WirelessStandardRecord(org_id=admin.org_id, settings=settings, updated_by_id=admin.id))
    else:
        row.settings = settings
        row.updated_by_id = admin.id
    await db.commit()
    return WorkbookImportOut(filename=filename, sha256=imported.sha256, profiles=profiles, replaced=replaced, standard=await get_standard(admin, db))


async def _save(db: AsyncSession, admin: User, standard: WirelessStandard, row: WirelessStandardRecord | None) -> None:
    settings = standard.model_dump_json()
    if row is None:
        db.add(WirelessStandardRecord(org_id=admin.org_id, settings=settings, updated_by_id=admin.id))
    else:
        row.settings = settings
        row.updated_by_id = admin.id
    await db.commit()


@router.post("/r2o/import", response_model=R2OImportOut)
async def import_r2o_checklist(
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> R2OImportOut:
    """Reads the Return-to-Operations checklist workbook (one sheet per
    controller build, one row per check). A sheet replaces the checklist of
    the same name; re-importing an updated workbook is how the checks
    follow the standard. Rows disabled by hand stay disabled."""
    content = await file.read(MAX_WORKBOOK_BYTES + 1)
    if len(content) > MAX_WORKBOOK_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The workbook is larger than 25 MB")
    filename = file.filename or "r2o.xlsx"
    try:
        sheets = await asyncio.to_thread(r2o_engine.import_checklist, content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not read {filename} as an Excel workbook: {exc}") from exc
    if not sheets:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No sheet has a 'What and how to get the relevant data' (command) column with checks under it")
    standard, row = await _load_standard(db, admin.org_id)
    now = datetime.now(timezone.utc)
    disabled = {item.item_id for c in standard.r2o_checklists for item in c.items if not item.enabled}
    checklists = [
        R2OChecklist(
            name=sheet.name, platform=sheet.platform, kind=sheet.kind, source=filename, imported_at=now,
            items=[
                R2OItemModel(
                    item_id=i.item_id, row=i.row, description=i.description, commands=i.commands, expected=i.expected, criteria=i.criteria,
                    stage=i.stage, level=i.level, comments=i.comments, enabled=i.item_id not in disabled,
                )
                for i in sheet.items
            ],
        )
        for sheet in sheets
    ]
    names = {c.name for c in checklists}
    replaced = [c.name for c in standard.r2o_checklists if c.name in names]
    standard.r2o_checklists = [c for c in standard.r2o_checklists if c.name not in names] + checklists
    import hashlib  # noqa: PLC0415

    sha = hashlib.sha256(content).hexdigest()
    standard.r2o_import_history = [
        ImportRecord(filename=filename, sha256=sha, imported_at=now, imported_by=admin.email, sheets=sorted(names), rules=sum(len(c.items) for c in checklists)),
        *standard.r2o_import_history,
    ][:MAX_IMPORT_HISTORY]
    await _save(db, admin, standard, row)
    return R2OImportOut(filename=filename, sha256=sha, checklists=checklists, replaced=replaced)


def _norm_cmd(command: str) -> str:
    return " ".join(command.lower().split())


@router.get("/r2o/commands", response_model=list[AuditCommandCoverageOut])
async def r2o_command_coverage(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[AuditCommandCoverageOut]:
    """For each controller type: the R2O checklist commands its command
    list does not run yet (matched exactly - the checks read their own
    command's output)."""
    standard, _ = await _load_standard(db, user.org_id)
    overrides = await get_org_command_overrides(db, user.org_id)
    counts = dict(
        (await db.execute(select(Device.device_type, func.count()).where(Device.org_id == user.org_id).group_by(Device.device_type))).all()
    )
    out = []
    for key, platform in wa.PLATFORMS.items():
        wanted = wa.r2o_commands(standard, platform)
        if not wanted:
            continue
        spec = DEVICE_TYPE_REGISTRY[key]
        override = overrides.get(key)
        commands = {_norm_cmd(c) for c in (parse_command_list(override.commands) if override else spec.default_commands)}
        out.append(
            AuditCommandCoverageOut(
                device_type=key, label=spec.label, device_count=int(counts.get(key, 0)), audit_commands=wanted,
                missing=[c for c in wanted if _norm_cmd(c) not in commands], is_custom_profile=override is not None,
            )
        )
    return out


@router.post("/r2o/commands/{device_type}", response_model=CommandProfileOut)
async def add_r2o_commands(device_type: str, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> CommandProfileOut:
    platform = wa.PLATFORMS.get(device_type)
    spec = DEVICE_TYPE_REGISTRY.get(device_type)
    if platform is None or spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="R2O checks only apply to wireless controllers")
    standard, _ = await _load_standard(db, admin.org_id)
    # Command lists are stored comma-separated, so a command with a comma in it cannot be added.
    wanted = [c for c in wa.r2o_commands(standard, platform) if "," not in c]
    override = await db.scalar(select(CommandProfile).where(CommandProfile.org_id == admin.org_id, CommandProfile.device_type == device_type))
    commands = parse_command_list(override.commands) if override else list(spec.default_commands)
    have = {_norm_cmd(c) for c in commands}
    # After everything else: "{ap}" commands need the AP list, "{client}" ones the clients.
    additions = [c for c in wanted if _norm_cmd(c) not in have]
    if additions:
        commands = commands + additions
        if override is None:
            override = CommandProfile(org_id=admin.org_id, device_type=device_type, commands=", ".join(commands))
            db.add(override)
        else:
            override.commands = ", ".join(commands)
        await db.commit()
    return CommandProfileOut(device_type=device_type, label=spec.label, category=spec.category, commands=commands, is_custom=override is not None)


# Lookups block a thread each (and a timed-out one keeps it until the
# resolver gives up), so they get their own pool: hundreds of AP names with
# no DNS record must not starve the threads the rest of the API runs on.
_DNS_POOL = ThreadPoolExecutor(max_workers=DNS_CONCURRENCY, thread_name_prefix="audit-dns")


async def _forward_lookup(name: str) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.run_in_executor(_DNS_POOL, socket.getaddrinfo, name, None)
    except OSError:
        return []
    return list(dict.fromkeys(info[4][0] for info in infos))


async def _reverse_lookup(ip: str) -> str | None:
    loop = asyncio.get_running_loop()
    try:
        hostname, _ = await loop.run_in_executor(_DNS_POOL, socket.getnameinfo, (ip, 0), socket.NI_NAMEREQD)
    except OSError:
        return None
    return hostname


async def resolve(names: set[str], ips: set[str], on_done=None) -> wa.DnsResults:
    """Forward lookups for `names` and PTR lookups for `ips`, bounded in
    concurrency and time so a site full of unregistered APs cannot hang
    the request."""
    semaphore = asyncio.Semaphore(DNS_CONCURRENCY)
    results = wa.DnsResults()
    done = 0

    def tick() -> None:
        nonlocal done
        done += 1
        if on_done is not None:
            on_done(done)

    async def forward(name: str) -> None:
        async with semaphore:
            try:
                results.forward[name] = await asyncio.wait_for(_forward_lookup(name), DNS_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                results.forward[name] = []
            tick()

    async def reverse(ip: str) -> None:
        async with semaphore:
            try:
                results.reverse[ip] = await asyncio.wait_for(_reverse_lookup(ip), DNS_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                results.reverse[ip] = None
            tick()

    tasks = {asyncio.ensure_future(forward(n)): n for n in names}
    tasks.update({asyncio.ensure_future(reverse(i)): i for i in ips})
    if tasks:
        _done, pending = await asyncio.wait(tasks, timeout=DNS_BUDGET_SECONDS)
        for task in pending:
            task.cancel()
            results.unchecked.add(tasks[task])
    return results


# A snapshot is a full collection when it has the controller's
# configuration in it; a client investigation (a few commands for one MAC)
# is not, and must not stand in for the controller's state.
_FULL_MARKERS = re.compile(r"^! ---- (?:sh\w*\s+run\w*(?:-config)?|show running-config|show run-config)\b", re.MULTILINE | re.IGNORECASE)


async def _audit_inputs(db: AsyncSession, org_id) -> list[inv.SnapshotInput]:
    """Every device's latest snapshot (as the inventory uses), except that a
    controller uses its latest *full* collection."""
    inputs = await _inputs(db, org_id)
    for i, item in enumerate(inputs):
        if item.device_type not in wa.PLATFORMS or (item.content and _FULL_MARKERS.search(item.content)):
            continue
        recent = await db.scalars(
            select(ConfigSnapshot).where(ConfigSnapshot.device_id == item.device_id).order_by(ConfigSnapshot.collected_at.desc()).limit(15)
        )
        for snap in recent:
            if _FULL_MARKERS.search(snap.content):
                inputs[i] = inv.SnapshotInput(item.device_id, item.name, item.host, item.device_type, item.site, item.role, snap.content, snap.collected_at, str(snap.id))
                break
    return inputs


# What each organization's audit is doing right now, for the page to show
# while it runs (GET /progress). In memory: an audit is one request.
_PROGRESS: dict[str, dict] = {}
_PROGRESS_LINES = 200


class _Progress:
    def __init__(self, org_id):
        self.key = str(org_id)
        self.state = {"running": True, "stage": "Starting", "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None, "error": None, "lines": []}
        _PROGRESS[self.key] = self.state

    def __call__(self, message: str, stage: str | None = None) -> None:
        if stage:
            self.state["stage"] = stage
        self.state["lines"] = (self.state["lines"] + [f"{datetime.now().strftime('%H:%M:%S')}  {message}"])[-_PROGRESS_LINES:]
        log.info("Wireless audit: %s", message)

    def finish(self, error: str | None = None) -> None:
        self.state.update(running=False, finished_at=datetime.now(timezone.utc).isoformat(), error=error, stage="Failed" if error else "Done")


async def _run(db: AsyncSession, org_id, with_dns: bool) -> dict:
    progress = _Progress(org_id)
    try:
        started = time.monotonic()
        progress("Loading the standard and the latest snapshots", stage="Loading snapshots")
        standard, _ = await _load_standard(db, org_id)
        inputs = await _audit_inputs(db, org_id)
        controllers = [i for i in inputs if i.device_type in wa.PLATFORMS]
        progress(f"{len(inputs)} device(s), {len(controllers)} wireless controller(s), {sum(len(i.content or '') for i in inputs) // 1_000_000} MB of snapshots ({time.monotonic() - started:.1f}s)")
        progress("Parsing the controllers' snapshots", stage="Parsing snapshots")
        t = time.monotonic()
        facts = await asyncio.to_thread(wa.gather, inputs, ignore_log_patterns=standard.log_ignore_patterns, progress=progress)
        progress(f"Parsed in {time.monotonic() - t:.1f}s: {sum(len(f.aps) for f in facts)} APs, {sum(len(f.clients) for f in facts)} clients")
        dns = None
        if with_dns:
            names, ips = wa.dns_targets(facts, standard)
            total = len(names) + len(ips)
            progress(f"Looking up {len(names)} names and {len(ips)} addresses in DNS", stage=f"DNS lookups (0/{total})")
            t = time.monotonic()

            def counted(done: int) -> None:
                progress.state["stage"] = f"DNS lookups ({done}/{total})"

            dns = await resolve(names, ips, on_done=counted)
            missing = sum(1 for k, v in dns.forward.items() if not v and k not in dns.unchecked)
            progress(
                f"DNS done in {time.monotonic() - t:.1f}s: {missing} name(s) with no record"
                + (f"; {len(dns.unchecked)} not looked up within {DNS_BUDGET_SECONDS:.0f}s (shown as not checked - this machine's DNS is slow)" if dns.unchecked else "")
            )
        else:
            progress("DNS lookups skipped (unticked)")
        progress("Running the checks", stage="Running checks")
        t = time.monotonic()
        report = await asyncio.to_thread(lambda: wa.run_audit(facts, standard, dns, now=datetime.now(timezone.utc), progress=progress))
        s = report["summary"]
        progress(f"Checks done in {time.monotonic() - t:.1f}s: {s['fail']} failure(s), {s['warn']} warning(s), {len(report.get('r2o', []))} R2O check(s). Total {time.monotonic() - started:.1f}s")
        progress.finish()
        return report
    except Exception as exc:
        log.exception("Wireless audit failed")
        progress(f"Failed: {exc}")
        progress.finish(str(exc))
        raise


@router.get("/progress")
async def audit_progress(user: User = Depends(get_current_user)) -> dict:
    """What this organization's audit is doing (or last did): stage and log lines."""
    return _PROGRESS.get(str(user.org_id)) or {"running": False, "stage": None, "started_at": None, "finished_at": None, "error": None, "lines": []}


@router.get("", response_model=WirelessAuditOut)
async def run_wireless_audit(
    dns: bool = Query(True, description="Look up every AP name and address in DNS"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _run(db, user.org_id, dns)


@router.get("/export.xlsx")
async def export_wireless_audit(
    dns: bool = Query(True),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    report = await _run(db, user.org_id, dns)
    org = await db.get(Organization, user.org_id)
    org_name = org.name if org else "Packrat"
    content = await asyncio.to_thread(build_audit_workbook, report, org_name)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", org_name).strip("-") or "packrat"
    filename = f"{safe}-wireless-audit-{report['generated_at'].strftime('%Y%m%d-%H%M')}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def client_commands(mac: str) -> dict[str, list[str]]:
    """What to collect to investigate one client, per controller type."""
    dotted = inv.normalize_mac(mac) or mac
    digits = dotted.replace(".", "")
    colon = ":".join(digits[i : i + 2] for i in range(0, 12, 2))
    return {
        "cisco_wlc_9800": [
            f"show wireless client mac-address {dotted} detail",
            f"show wireless client mac-address {dotted} mobility history",
            f"show logging | include {dotted}",
            "show wireless stats client delete reasons",
        ],
        "cisco_wlc": [
            f"show client detail {colon}",
            f"show client roam-history {colon}",
            "show msglog",
            "show traplog",
        ],
    }


@router.get("/clients/{mac}", response_model=ClientLookupOut)
async def lookup_client(
    mac: str,
    days: int = Query(7, ge=1, le=90, description="How far back to search the controllers' snapshots"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ClientLookupOut:
    """Everything the controllers' recent snapshots say about one client
    MAC (any spelling): log messages classified as joins, roams, drops and
    failures, its roam history and counters, merged across snapshots."""
    key = inv.normalize_mac(mac)
    if key is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{mac}' is not a MAC address")
    controllers = list(await db.scalars(select(Device).where(Device.org_id == user.org_id, Device.device_type.in_(list(wa.PLATFORMS)))))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    searched = 0
    histories = []
    for device in controllers:
        snaps = list(
            await db.scalars(
                select(ConfigSnapshot)
                .where(ConfigSnapshot.device_id == device.id, ConfigSnapshot.collected_at >= since)
                .order_by(ConfigSnapshot.collected_at.desc())
                .limit(30)
            )
        )
        searched += len(snaps)
        merged: ce.ClientHistory | None = None
        seen_lines: set[str] = set()
        connected = False
        for index, snap in enumerate(snaps):
            if key not in (inv.normalize_mac(m.group(0)) for m in ce._MAC_RE.finditer(snap.content)):
                continue
            h = (await asyncio.to_thread(ce.histories, snap.content)).get(key)
            if h is None:
                continue
            if merged is None:
                merged = ce.ClientHistory(key)
            for e in h.events:
                if e.text not in seen_lines:
                    seen_lines.add(e.text)
                    merged.events.append(e)
            merged.roams = merged.roams or h.roams
            merged.counters = merged.counters or h.counters
            if index == 0:
                connected = bool(h.counters) or key in (inv.normalize_mac(m.group(0)) for m in ce._MAC_RE.finditer(inv.find_section(snap.content, "show wireless client summary") or inv.find_section(snap.content, "show client summary") or ""))
        if merged is None:
            continue
        merged.events.sort(key=lambda e: e.time or "")
        standard, _ = await _load_standard(db, user.org_id)
        issues = [m for _, m in ce.issues_for(merged, roam_warn=standard.client_roam_warn, retry_warn_percent=standard.client_retry_warn_percent)]
        facts = wa.ControllerFacts(input=inv.SnapshotInput(str(device.id), device.name, device.host, device.device_type, device.site, None, None), platform=wa.PLATFORMS[device.device_type])
        histories.append(wa.history_out(facts, merged, connected, issues))
    return ClientLookupOut(mac=key, snapshots_searched=searched, histories=histories, commands=client_commands(key))


@router.get("/commands", response_model=list[AuditCommandCoverageOut])
async def audit_command_coverage(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[AuditCommandCoverageOut]:
    """For each controller type: the audit commands its command list does
    not run yet."""
    overrides = await get_org_command_overrides(db, user.org_id)
    counts = dict(
        (await db.execute(select(Device.device_type, func.count()).where(Device.org_id == user.org_id).group_by(Device.device_type))).all()
    )
    out = []
    for key, wanted in wa.AUDIT_COMMANDS.items():
        spec = DEVICE_TYPE_REGISTRY[key]
        override = overrides.get(key)
        commands = parse_command_list(override.commands) if override else list(spec.default_commands)
        out.append(
            AuditCommandCoverageOut(
                device_type=key,
                label=spec.label,
                device_count=int(counts.get(key, 0)),
                audit_commands=list(wanted),
                missing=[c for c in wanted if not _covered(commands, c)],
                is_custom_profile=override is not None,
            )
        )
    return out


@router.post("/commands/{device_type}", response_model=CommandProfileOut)
async def add_audit_commands(device_type: str, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> CommandProfileOut:
    """Appends the missing audit commands to the org's command list for
    this controller type. A per-client "{client}" command must come after
    the client summary it expands from, so additions go at the end."""
    wanted = wa.AUDIT_COMMANDS.get(device_type)
    spec = DEVICE_TYPE_REGISTRY.get(device_type)
    if wanted is None or spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No audit commands are defined for this device type")
    override = await db.scalar(select(CommandProfile).where(CommandProfile.org_id == admin.org_id, CommandProfile.device_type == device_type))
    commands = parse_command_list(override.commands) if override else list(spec.default_commands)
    additions = [c for c in wanted if not _covered(commands, c)]
    if additions:
        commands = commands + additions
        if override is None:
            override = CommandProfile(org_id=admin.org_id, device_type=device_type, commands=", ".join(commands))
            db.add(override)
        else:
            override.commands = ", ".join(commands)
        await db.commit()
    return CommandProfileOut(device_type=device_type, label=spec.label, category=spec.category, commands=commands, is_custom=override is not None)
