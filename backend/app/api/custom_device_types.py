"""Org-defined device types (see models/custom_device_type.py) - the way a
user adds a platform the built-in registry doesn't know, with whatever
commands they want collected from it (config, logs, inventory...)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.database import get_db
from app.models.custom_device_type import CustomDeviceType
from app.models.device import Device
from app.models.user import User
from app.schemas.custom_device_type import (
    CATEGORIES,
    CustomDeviceTypeCreate,
    CustomDeviceTypeOut,
    CustomDeviceTypeUpdate,
    NetmikoDriversOut,
)
from app.services.device_types import Catalog, build_catalog, list_netmiko_drivers, parse_command_list

router = APIRouter(prefix="/api/custom-device-types", tags=["custom-device-types"])


async def load_custom_types(db: AsyncSession, org_id) -> list[CustomDeviceType]:
    return list(
        await db.scalars(
            select(CustomDeviceType).where(CustomDeviceType.org_id == org_id).order_by(CustomDeviceType.label)
        )
    )


async def load_catalog(db: AsyncSession, org_id) -> Catalog:
    """Built-in registry + this org's custom types, for anything that needs
    to validate or resolve a device_type in a request (devices, jobs, the
    device-types listing). The worker builds the same thing from its sync
    session - see tasks.py's _load_catalog."""
    return build_catalog(await load_custom_types(db, org_id))


async def _device_counts(db: AsyncSession, org_id) -> dict[str, int]:
    rows = await db.execute(
        select(Device.device_type, func.count()).where(Device.org_id == org_id).group_by(Device.device_type)
    )
    return dict(rows.all())


def _to_out(row: CustomDeviceType, device_count: int) -> CustomDeviceTypeOut:
    return CustomDeviceTypeOut(
        id=row.id,
        key=row.key,
        label=row.label,
        category=row.category,
        netmiko_driver=row.netmiko_driver,
        default_commands=parse_command_list(row.default_commands or ""),
        secret_supported=row.secret_supported,
        timing_read=row.timing_read,
        device_count=device_count,
        created_at=row.created_at,
    )


def _check_driver(name: str) -> None:
    if name not in list_netmiko_drivers():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{name}' is not a Netmiko SSH driver - see GET /api/custom-device-types/netmiko-drivers",
        )


@router.get("/netmiko-drivers", response_model=NetmikoDriversOut)
async def get_netmiko_drivers(user: User = Depends(get_current_user)) -> NetmikoDriversOut:
    return NetmikoDriversOut(drivers=list_netmiko_drivers(), categories=list(CATEGORIES))


@router.get("", response_model=list[CustomDeviceTypeOut])
async def list_custom_device_types(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CustomDeviceTypeOut]:
    counts = await _device_counts(db, user.org_id)
    return [_to_out(row, counts.get(row.key, 0)) for row in await load_custom_types(db, user.org_id)]


@router.post("", response_model=CustomDeviceTypeOut, status_code=status.HTTP_201_CREATED)
async def create_custom_device_type(
    payload: CustomDeviceTypeCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomDeviceTypeOut:
    _check_driver(payload.netmiko_driver)
    existing = await db.scalar(
        select(CustomDeviceType).where(CustomDeviceType.org_id == admin.org_id, CustomDeviceType.key == payload.key)
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"A device type with key '{payload.key}' already exists")
    row = CustomDeviceType(org_id=admin.org_id, **payload.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_out(row, 0)


async def _get_owned(db: AsyncSession, type_id: UUID, org_id) -> CustomDeviceType:
    row = await db.get(CustomDeviceType, type_id)
    if row is None or row.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device type not found")
    return row


@router.patch("/{type_id}", response_model=CustomDeviceTypeOut)
async def update_custom_device_type(
    type_id: UUID,
    payload: CustomDeviceTypeUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomDeviceTypeOut:
    row = await _get_owned(db, type_id, admin.org_id)
    updates = payload.model_dump(exclude_unset=True)
    if "netmiko_driver" in updates:
        _check_driver(updates["netmiko_driver"])
    for field, value in updates.items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    counts = await _device_counts(db, admin.org_id)
    return _to_out(row, counts.get(row.key, 0))


@router.delete("/{type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_device_type(
    type_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    row = await _get_owned(db, type_id, admin.org_id)
    in_use = await db.scalar(
        select(func.count()).select_from(Device).where(Device.org_id == admin.org_id, Device.device_type == row.key)
    )
    if in_use:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{in_use} device(s) still use '{row.label}' - change their type or delete them first",
        )
    await db.delete(row)
    await db.commit()
