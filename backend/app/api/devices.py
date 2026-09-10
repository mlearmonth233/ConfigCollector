import csv
import io
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.credential import Credential
from app.models.device import Device, NetworkZone
from app.models.job import ACTIVE_JOB_STATUSES, CollectionJobItem
from app.models.snapshot import ConfigSnapshot
from app.models.user import User
from app.schemas.device import DeviceCreate, DeviceDetectionOut, DeviceImportResult, DeviceOut, DeviceUpdate
from app.services.device_types import DEVICE_TYPE_REGISTRY
from app.services.hostname_detection import DEVICE_ROLES, detect, device_type_for_role

router = APIRouter(prefix="/api/devices", tags=["devices"])

# Columns expected in a bulk-import CSV. credential_name is looked up by name
# within the caller's org; leave blank to add the device without one.
# device_type/device_role/network_zone are all optional - left blank, they're
# auto-detected from `name` (see app.services.hostname_detection); an
# explicit value in the CSV always wins over a detected one.
CSV_COLUMNS = [
    "name",
    "host",
    "port",
    "device_type",
    "site",
    "credential_name",
    "custom_commands",
    "device_role",
    "network_zone",
]

# Example rows for the downloadable template - mirrors the README's own CSV
# example so the two never drift apart. Blank device_type/device_role/
# network_zone (the last row) demonstrates leaving them to be auto-detected
# from the name (see app.services.hostname_detection).
_CSV_TEMPLATE_ROWS = [
    ["core-sw1", "10.0.0.1", "22", "cisco_ios", "DC1", "labcred", "", "", ""],
    ["wlc-1", "10.0.0.2", "22", "cisco_wlc", "DC1", "labcred", "", "", ""],
    ["pdu-1", "10.0.0.4", "22", "pdu_generic", "DC1", "labcred", "about,show status", "", ""],
    ["GBGYSP01SWA001", "10.0.0.5", "22", "", "", "labcred", "", "", ""],
]


@router.get("", response_model=list[DeviceOut])
async def list_devices(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Device]:
    result = await db.scalars(select(Device).where(Device.org_id == user.org_id))
    return list(result)


@router.get("/import-template")
async def download_import_template(user: User = Depends(get_current_user)) -> Response:
    """A ready-to-edit CSV matching CSV_COLUMNS, with a few example rows -
    the last one leaves device_type/device_role/network_zone blank to show
    off name-based auto-detection."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    writer.writerows(_CSV_TEMPLATE_ROWS)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="device_import_template.csv"'},
    )


@router.get("/detect", response_model=DeviceDetectionOut)
async def detect_device(
    name: str = Query(min_length=1),
    user: User = Depends(get_current_user),
) -> DeviceDetectionOut:
    """Best-effort role/zone/device_type guess from a device name, for the
    "Add device" form (and CSV import) to pre-fill - always just a
    starting point, never applied without the caller's say-so."""
    result = detect(name)
    return DeviceDetectionOut(
        device_role=result.device_role,
        device_role_label=result.device_role_label,
        network_zone=result.network_zone,
        suggested_device_type=result.suggested_device_type,
    )


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def create_device(
    payload: DeviceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Device:
    data = payload.model_dump()
    try:
        device_role, network_zone, device_type = _resolve_detected_fields(
            name=data["name"],
            device_role=data.get("device_role"),
            network_zone=data.get("network_zone"),
            device_type=data.get("device_type"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    data["device_role"] = device_role
    data["network_zone"] = network_zone
    data["device_type"] = device_type

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
    if "device_type" in updates:
        _validate_device_type(updates["device_type"])
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
            detail="This device has a collection job in progress - wait for it to finish (or cancel it) before deleting",
        )

    # Past job items/snapshots reference this device but should outlive it -
    # they're history, not a live pointer - so this device_id is nulled out
    # rather than deleting (or blocking on) those rows. Done as plain UPDATEs
    # rather than through the ORM relationship, which would otherwise need
    # to load every job_item just to null each one individually.
    await db.execute(update(CollectionJobItem).where(CollectionJobItem.device_id == device_id).values(device_id=None))
    await db.execute(update(ConfigSnapshot).where(ConfigSnapshot.device_id == device_id).values(device_id=None))

    await db.delete(device)
    await db.commit()


@router.post("/import", response_model=DeviceImportResult)
async def import_devices(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeviceImportResult:
    raw = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))

    if reader.fieldnames is None or "name" not in reader.fieldnames or "host" not in reader.fieldnames:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CSV must have a header row including at least: name, host. Expected columns: {', '.join(CSV_COLUMNS)}",
        )

    credentials_by_name = {
        c.name: c.id for c in await db.scalars(select(Credential).where(Credential.org_id == user.org_id))
    }

    created = 0
    errors: list[str] = []
    for line_no, row in enumerate(reader, start=2):
        try:
            name = (row.get("name") or "").strip()
            host = (row.get("host") or "").strip()
            if not name or not host:
                raise ValueError("name and host are required")

            raw_zone = (row.get("network_zone") or "").strip()
            network_zone = None
            if raw_zone:
                try:
                    network_zone = NetworkZone(raw_zone.lower())
                except ValueError as exc:
                    raise ValueError(
                        f"unknown network_zone '{raw_zone}'. Valid values: {', '.join(z.value for z in NetworkZone)}"
                    ) from exc

            raw_role = (row.get("device_role") or "").strip()
            device_role = None
            if raw_role:
                if raw_role not in DEVICE_ROLES:
                    raise ValueError(f"unknown device_role '{raw_role}'. Valid values: {', '.join(DEVICE_ROLES)}")
                device_role = raw_role

            device_role, network_zone, device_type = _resolve_detected_fields(
                name=name,
                device_role=device_role,
                network_zone=network_zone,
                device_type=(row.get("device_type") or "").strip() or None,
            )

            credential_id = None
            credential_name = (row.get("credential_name") or "").strip()
            if credential_name:
                credential_id = credentials_by_name.get(credential_name)
                if credential_id is None:
                    raise ValueError(f"unknown credential_name '{credential_name}'")

            port_raw = (row.get("port") or "").strip()
            device = Device(
                org_id=user.org_id,
                name=name,
                host=host,
                port=int(port_raw) if port_raw else 22,
                device_type=device_type,
                site=(row.get("site") or "").strip() or None,
                credential_id=credential_id,
                custom_commands=(row.get("custom_commands") or "").strip() or None,
                device_role=device_role,
                network_zone=network_zone,
            )
            db.add(device)
            created += 1
        except ValueError as exc:
            errors.append(f"line {line_no}: {exc}")

    await db.commit()
    return DeviceImportResult(created=created, errors=errors)


def _resolve_detected_fields(
    *,
    name: str,
    device_role: str | None,
    network_zone: NetworkZone | None,
    device_type: str | None,
) -> tuple[str | None, NetworkZone | None, str]:
    """Fills in device_role/network_zone/device_type from the device's name
    wherever the caller didn't supply one explicitly - an explicit value
    always wins. Raises ValueError (callers translate to a 400 or a
    per-row CSV import error) if device_type still can't be determined."""
    guess = detect(name)
    effective_role = device_role or guess.device_role
    effective_zone = network_zone or guess.network_zone

    if device_type:
        effective_type = device_type
    elif device_role:
        # An explicitly-given role is the source of truth for the
        # type-from-role mapping, even if it disagrees with what the name
        # would have suggested.
        effective_type = device_type_for_role(device_role)
    else:
        effective_type = guess.suggested_device_type

    if not effective_type:
        if effective_role == "wlc":
            hint = " - a WLC could be AireOS ('cisco_wlc') or Catalyst 9800 ('cisco_wlc_9800')"
        else:
            hint = " - no recognizable role (access/core/distribution/server switch, WLC, PDU, console server) was found in the name either"
        raise ValueError(f"device_type could not be determined automatically for '{name}'{hint} - specify one")

    _check_device_type(effective_type)
    return effective_role, effective_zone, effective_type


def _check_device_type(device_type: str) -> None:
    """Raises ValueError (not HTTPException) so callers - including the
    per-row CSV import loop, which catches ValueError to record a row error
    without aborting the rest of the batch - can handle it appropriately."""
    if device_type not in DEVICE_TYPE_REGISTRY:
        raise ValueError(f"unknown device_type '{device_type}'. Valid values: {', '.join(DEVICE_TYPE_REGISTRY)}")


def _validate_device_type(device_type: str) -> None:
    try:
        _check_device_type(device_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _get_owned_device(db: AsyncSession, device_id: UUID, org_id: UUID) -> Device:
    device = await db.get(Device, device_id)
    if device is None or device.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device
