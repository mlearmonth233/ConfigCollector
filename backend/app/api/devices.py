import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.services.licensing import check_device_limit
from app.database import get_db
from app.models.credential import Credential
from app.models.device import Device
from app.models.ping_monitor import PingSample, PingStatus
from app.models.job import ACTIVE_JOB_STATUSES, CollectionJobItem
from app.models.snapshot import ConfigSnapshot
from app.models.user import User
from app.schemas.device import (
    DeviceClearResult,
    DeviceCreate,
    DeviceDetectionOut,
    DeviceOut,
    DeviceReachabilityOut,
    DeviceUpdate,
)
from app.api.custom_device_types import load_catalog
from app.services.device_types import Catalog
from app.api.hostname_rules import load_rules
from app.services.hostname_detection import Rule, detect, device_type_for_role
from app.services.reachability import check_reachability

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("", response_model=list[DeviceOut])
async def list_devices(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Device]:
    result = await db.scalars(select(Device).where(Device.org_id == user.org_id))
    return list(result)


@router.delete("", response_model=DeviceClearResult)
async def clear_all_devices(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeviceClearResult:
    """Bulk 'wipe the device list': deletes every device in the org except
    one currently mid-collection (same guard as delete_device, applied
    across the board rather than erroring the whole request over one busy
    device). Like a single delete, this only removes the device itself -
    its past job items/snapshots outlive it as orphaned history (device_id
    nulled out), exactly as delete_device already does."""
    devices = list(await db.scalars(select(Device).where(Device.org_id == user.org_id)))
    if not devices:
        return DeviceClearResult(deleted=0, skipped=0)

    device_ids = [d.id for d in devices]
    active_device_ids = set(
        await db.scalars(
            select(CollectionJobItem.device_id)
            .where(CollectionJobItem.device_id.in_(device_ids), CollectionJobItem.status.in_(ACTIVE_JOB_STATUSES))
            .distinct()
        )
    )
    to_delete = [d for d in devices if d.id not in active_device_ids]
    to_delete_ids = [d.id for d in to_delete]

    if to_delete_ids:
        await db.execute(
            update(CollectionJobItem).where(CollectionJobItem.device_id.in_(to_delete_ids)).values(device_id=None)
        )
        await db.execute(
            update(ConfigSnapshot).where(ConfigSnapshot.device_id.in_(to_delete_ids)).values(device_id=None)
        )
        # Reachability monitor rows are pure derived state: drop them.
        await db.execute(delete(PingSample).where(PingSample.device_id.in_(to_delete_ids)))
        await db.execute(delete(PingStatus).where(PingStatus.device_id.in_(to_delete_ids)))
        for device in to_delete:
            await db.delete(device)
        await db.commit()

    return DeviceClearResult(deleted=len(to_delete), skipped=len(active_device_ids))


@router.get("/detect", response_model=DeviceDetectionOut)
async def detect_device(
    name: str = Query(min_length=1),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeviceDetectionOut:
    """Best-effort role/device_type guess from a device name, per the
    org's naming rules (see /api/hostname-rules), for the "Add device" form
    and bulk add to pre-fill - always just a starting point, never applied
    without the caller's say-so."""
    result = detect(name, await load_rules(db, user.org_id))
    return DeviceDetectionOut(
        device_role=result.device_role,
        device_role_label=result.device_role_label,
        suggested_device_type=result.suggested_device_type,
    )


@router.get("/reachability", response_model=list[DeviceReachabilityOut])
async def check_devices_reachability(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DeviceReachabilityOut]:
    """Pings every device in the org and looks up its hostname in DNS, purely
    to surface a visual heads-up - neither check is treated as proof a
    device is actually offline (a firewall commonly drops ICMP for a device
    that's perfectly reachable over SSH, and a name can be missing from DNS
    for a device that's still reachable some other way), so this never marks
    anything "offline", only reports what each check saw."""
    devices = list(await db.scalars(select(Device).where(Device.org_id == user.org_id)))
    results = await asyncio.gather(*(check_reachability(d.host) for d in devices))
    return [
        DeviceReachabilityOut(
            device_id=d.id, host=d.host, ping_ok=r.ping_ok, dns_ok=r.dns_ok, resolved_ip=r.resolved_ip
        )
        for d, r in zip(devices, results)
    ]


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def create_device(
    payload: DeviceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Device:
    await check_device_limit(db, user.org_id)
    data = payload.model_dump()
    catalog = await load_catalog(db, user.org_id)
    rules = await load_rules(db, user.org_id)
    try:
        device_role, device_type = _resolve_detected_fields(
            name=data["name"],
            device_role=data.get("device_role"),
            device_type=data.get("device_type"),
            catalog=catalog,
            rules=rules,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    data["device_role"] = device_role
    data["device_type"] = device_type
    _normalize_console(data)
    await _check_console_credential(db, data.get("console_credential_id"), user.org_id)

    device = Device(org_id=user.org_id, **data)
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device


@router.patch("/{device_id}", response_model=DeviceOut)
async def update_device(
    device_id: UUID,
    payload: DeviceUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Device:
    device = await _get_owned_device(db, device_id, user.org_id)
    updates = payload.model_dump(exclude_unset=True)
    if updates.pop("clear_snmp_profile", False):
        updates["snmp_profile_id"] = None
    if updates.pop("clear_console", False):
        updates.update(console_host=None, console_port=None, console_protocol=None, console_credential_id=None, console_connect_command=None)
    elif any(k.startswith("console_") for k in updates):
        merged = {k: updates.get(k, getattr(device, k)) for k in CONSOLE_KEYS}
        _normalize_console(merged)
        updates.update(merged)
    await _check_console_credential(db, updates.get("console_credential_id"), user.org_id)
    if "device_type" in updates:
        _validate_device_type(updates["device_type"], await load_catalog(db, user.org_id))
    for field, value in updates.items():
        setattr(device, field, value)
    await db.commit()
    await db.refresh(device)
    return device


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    device_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    device = await _get_owned_device(db, device_id, user.org_id)

    in_progress = await db.scalar(
        select(CollectionJobItem.id)
        .where(CollectionJobItem.device_id == device_id, CollectionJobItem.status.in_(ACTIVE_JOB_STATUSES))
        .limit(1)
    )
    if in_progress is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This device has a collection job in progress - wait for it to finish (or cancel it) before deleting. "
                "If the job is stuck because the worker was interrupted, open it on the Jobs page and use Force stop."
            ),
        )

    # Past job items/snapshots reference this device but should outlive it -
    # they're history, not a live pointer - so this device_id is nulled out
    # rather than deleting (or blocking on) those rows. Done as plain UPDATEs
    # rather than through the ORM relationship, which would otherwise need
    # to load every job_item just to null each one individually.
    await db.execute(update(CollectionJobItem).where(CollectionJobItem.device_id == device_id).values(device_id=None))
    await db.execute(update(ConfigSnapshot).where(ConfigSnapshot.device_id == device_id).values(device_id=None))
    await db.execute(delete(PingSample).where(PingSample.device_id == device_id))
    await db.execute(delete(PingStatus).where(PingStatus.device_id == device_id))

    await db.delete(device)
    await db.commit()


def _resolve_detected_fields(
    *,
    name: str,
    device_role: str | None,
    device_type: str | None,
    catalog: Catalog,
    rules: tuple[Rule, ...] | None = None,
) -> tuple[str | None, str]:
    """Fills in device_role/device_type from the device's name
    wherever the caller didn't supply one explicitly - an explicit value
    always wins. Raises ValueError (callers translate to a 400 or a
    per-row CSV import error) if device_type still can't be determined."""
    guess = detect(name, rules)
    effective_role = device_role or guess.device_role

    if device_type:
        effective_type = device_type
    elif device_role:
        # An explicitly-given role is the source of truth for the
        # type-from-role mapping, even if it disagrees with what the name
        # would have suggested.
        effective_type = device_type_for_role(device_role, rules)
    else:
        effective_type = guess.suggested_device_type

    if not effective_type:
        if effective_role == "wlc":
            hint = " - a WLC could be AireOS ('cisco_wlc') or Catalyst 9800 ('cisco_wlc_9800')"
        elif effective_role == "console_server":
            hint = " - no console-server device type is currently configured; pick one manually"
        elif effective_role:
            hint = f" - '{effective_role}' has no device type mapped to it"
        else:
            hint = " - no recognizable role (access/core/distribution/server switch, WLC, PDU) was found in the name either"
        raise ValueError(f"device_type could not be determined automatically for '{name}'{hint} - specify one")

    _check_device_type(effective_type, catalog)
    return effective_role, effective_type


def _check_device_type(device_type: str, catalog: Catalog) -> None:
    """Raises ValueError (not HTTPException) so callers that batch rows can
    record a per-row error without aborting the rest. `catalog` is the
    built-in registry plus the org's own custom types (see
    custom_device_types.load_catalog), so a user-defined type is as valid
    here as a built-in one."""
    if device_type not in catalog:
        raise ValueError(f"unknown device_type '{device_type}'. Valid values: {', '.join(catalog)}")


def _validate_device_type(device_type: str, catalog: Catalog) -> None:
    try:
        _check_device_type(device_type, catalog)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _get_owned_device(db: AsyncSession, device_id: UUID, org_id: UUID) -> Device:
    device = await db.get(Device, device_id)
    if device is None or device.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


CONSOLE_KEYS = ("console_host", "console_port", "console_protocol", "console_credential_id", "console_connect_command")


def _normalize_console(data: dict) -> None:
    """A console path is host + port + protocol together. Blank host clears
    the lot; a host without a protocol defaults to SSH; a host without a
    port gets the protocol's default (22 / 23)."""
    host = (data.get("console_host") or "").strip() or None
    if host is None:
        for key in CONSOLE_KEYS:
            data[key] = None
        return
    protocol = data.get("console_protocol") or "ssh"
    data["console_host"] = host
    data["console_protocol"] = protocol
    data["console_port"] = data.get("console_port") or (22 if protocol == "ssh" else 23)
    data["console_connect_command"] = (data.get("console_connect_command") or "").strip() or None
    if protocol == "telnet":
        data["console_credential_id"] = None


async def _check_console_credential(db: AsyncSession, credential_id, org_id) -> None:
    if credential_id is None:
        return
    owned = await db.scalar(select(Credential.id).where(Credential.id == credential_id, Credential.org_id == org_id))
    if owned is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Console credential not found")
