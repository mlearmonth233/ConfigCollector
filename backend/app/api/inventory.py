"""Inventory: hardware facts read out of the latest collected snapshots,
as JSON for the page and as an Excel workbook to download."""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.command_profiles import get_org_command_overrides
from app.api.deps import get_current_user, require_admin
from app.database import get_db
from app.models.command_profile import CommandProfile
from app.models.device import Device
from app.models.organization import Organization
from app.models.snapshot import ConfigSnapshot
from app.models.user import User
from app.schemas.command_profile import CommandProfileOut
from app.schemas.inventory import InventoryCommandCoverageOut, InventoryOut, InventorySummaryOut
from app.services import inventory as inv
from app.services.device_types import DEVICE_TYPE_REGISTRY, parse_command_list
from app.services.inventory_export import build_workbook

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


async def _inputs(db: AsyncSession, org_id) -> list[inv.SnapshotInput]:
    """Every device in the org with its most recent snapshot (if any)."""
    devices = list(await db.scalars(select(Device).where(Device.org_id == org_id).order_by(Device.name)))
    if not devices:
        return []
    device_ids = [d.id for d in devices]
    latest = (
        select(ConfigSnapshot.device_id, func.max(ConfigSnapshot.collected_at).label("latest"))
        .where(ConfigSnapshot.device_id.in_(device_ids))
        .group_by(ConfigSnapshot.device_id)
        .subquery()
    )
    rows = await db.execute(
        select(ConfigSnapshot).join(latest, (ConfigSnapshot.device_id == latest.c.device_id) & (ConfigSnapshot.collected_at == latest.c.latest))
    )
    snapshots = {}
    for snap in rows.scalars():
        snapshots.setdefault(snap.device_id, snap)  # two in the same second: keep the first
    return [
        inv.SnapshotInput(
            device_id=str(d.id),
            name=d.name,
            host=d.host,
            device_type=d.device_type,
            site=d.site,
            role=d.device_role.value if hasattr(d.device_role, "value") else d.device_role,
            content=snapshots[d.id].content if d.id in snapshots else None,
            collected_at=snapshots[d.id].collected_at if d.id in snapshots else None,
            snapshot_id=str(snapshots[d.id].id) if d.id in snapshots else None,
        )
        for d in devices
    ]


def _to_out(built: inv.Inventory) -> InventoryOut:
    return InventoryOut(
        generated_at=built.generated_at,
        summary=InventorySummaryOut(
            devices=len(built.devices),
            devices_with_config=sum(1 for d in built.devices if d.has_snapshot),
            devices_with_serial=sum(1 for d in built.devices if d.serial),
            hardware=len(built.hardware),
            access_points=len(built.access_points),
            neighbors=len(built.neighbors),
            unmanaged=len(built.unmanaged),
            endpoints=sum(1 for e in built.endpoints if not e.on_uplink),
        ),
        models=built.models,
        devices=[asdict(d) for d in built.devices],
        hardware=[asdict(h) for h in built.hardware],
        neighbors=[asdict(n) for n in built.neighbors],
        access_points=[asdict(a) for a in built.access_points],
        endpoints=[asdict(e) for e in built.endpoints],
        unmanaged=[asdict(u) for u in built.unmanaged],
    )


async def _build(db: AsyncSession, org_id) -> inv.Inventory:
    inputs = await _inputs(db, org_id)
    # Parsing a few hundred configs is CPU work: keep it off the event loop.
    return await asyncio.to_thread(inv.build_inventory, inputs, now=datetime.now(timezone.utc))


@router.get("", response_model=InventoryOut)
async def get_inventory(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> InventoryOut:
    return _to_out(await _build(db, user.org_id))


@router.get("/export.xlsx")
async def export_inventory(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Response:
    built = await _build(db, user.org_id)
    org = await db.get(Organization, user.org_id)
    org_name = org.name if org else "Packrat"
    content = await asyncio.to_thread(build_workbook, built, org_name)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", org_name).strip("-") or "packrat"
    filename = f"{safe}-inventory-{built.generated_at.strftime('%Y%m%d-%H%M')}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _covered(commands: list[str], canonical: str) -> bool:
    return any(inv.command_matches(c, canonical) for c in commands)


@router.get("/commands", response_model=list[InventoryCommandCoverageOut])
async def command_coverage(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[InventoryCommandCoverageOut]:
    """For each built-in device type the org uses: which inventory commands
    its current command list does not run yet."""
    overrides = await get_org_command_overrides(db, user.org_id)
    counts = dict(
        (await db.execute(select(Device.device_type, func.count()).where(Device.org_id == user.org_id).group_by(Device.device_type))).all()
    )
    out = []
    for key, wanted in inv.INVENTORY_COMMANDS.items():
        spec = DEVICE_TYPE_REGISTRY.get(key)
        if spec is None:
            continue
        override = overrides.get(key)
        commands = parse_command_list(override.commands) if override else list(spec.default_commands)
        out.append(
            InventoryCommandCoverageOut(
                device_type=key,
                label=spec.label,
                device_count=int(counts.get(key, 0)),
                inventory_commands=list(wanted),
                missing=[c for c in wanted if not _covered(commands, c)],
                is_custom_profile=override is not None,
            )
        )
    return out


@router.post("/commands/{device_type}", response_model=CommandProfileOut)
async def add_inventory_commands(device_type: str, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> CommandProfileOut:
    """Appends the missing inventory commands to the org's command list for
    this type (creating an override of the built-in default if needed).
    Existing commands are kept in their order."""
    wanted = inv.INVENTORY_COMMANDS.get(device_type)
    spec = DEVICE_TYPE_REGISTRY.get(device_type)
    if wanted is None or spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No inventory commands are defined for this device type")
    override = await db.scalar(select(CommandProfile).where(CommandProfile.org_id == admin.org_id, CommandProfile.device_type == device_type))
    commands = parse_command_list(override.commands) if override else list(spec.default_commands)
    additions = [c for c in wanted if not _covered(commands, c)]
    if additions:
        # Put them before a trailing "show run"-style dump? Order rarely
        # matters to the device; keep it simple and append.
        commands = commands + additions
        if override is None:
            override = CommandProfile(org_id=admin.org_id, device_type=device_type, commands=", ".join(commands))
            db.add(override)
        else:
            override.commands = ", ".join(commands)
        await db.commit()
    return CommandProfileOut(device_type=device_type, label=spec.label, category=spec.category, commands=commands, is_custom=override is not None)
