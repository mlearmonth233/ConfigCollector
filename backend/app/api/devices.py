import csv
import io
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.credential import Credential
from app.models.device import Device
from app.models.user import User
from app.schemas.device import DeviceCreate, DeviceImportResult, DeviceOut, DeviceUpdate
from app.services.device_types import DEVICE_TYPE_REGISTRY

router = APIRouter(prefix="/api/devices", tags=["devices"])

# Columns expected in a bulk-import CSV. credential_name is looked up by name
# within the caller's org; leave blank to add the device without one.
CSV_COLUMNS = ["name", "host", "port", "device_type", "site", "credential_name", "custom_commands"]


@router.get("", response_model=list[DeviceOut])
async def list_devices(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Device]:
    result = await db.scalars(select(Device).where(Device.org_id == user.org_id))
    return list(result)


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def create_device(
    payload: DeviceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Device:
    _validate_device_type(payload.device_type)
    device = Device(org_id=user.org_id, **payload.model_dump())
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
            detail=f"CSV must have a header row including at least: name, host, device_type. Expected columns: {', '.join(CSV_COLUMNS)}",
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
            device_type = (row.get("device_type") or "").strip()
            if not name or not host or not device_type:
                raise ValueError("name, host, and device_type are required")
            _check_device_type(device_type)

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
            )
            db.add(device)
            created += 1
        except ValueError as exc:
            errors.append(f"line {line_no}: {exc}")

    await db.commit()
    return DeviceImportResult(created=created, errors=errors)


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
