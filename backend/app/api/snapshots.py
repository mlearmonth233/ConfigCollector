from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.device import Device
from app.models.snapshot import ConfigSnapshot
from app.models.user import User
from app.schemas.snapshot import SnapshotOut, SnapshotSummaryOut
from app.services.filenames import build_snapshot_filename

router = APIRouter(prefix="/api", tags=["snapshots"])


@router.get("/devices/{device_id}/snapshots", response_model=list[SnapshotSummaryOut])
async def list_device_snapshots(
    device_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConfigSnapshot]:
    await _get_owned_device(db, device_id, user.org_id)
    result = await db.scalars(
        select(ConfigSnapshot)
        .where(ConfigSnapshot.device_id == device_id)
        .order_by(ConfigSnapshot.collected_at.desc())
    )
    return list(result)


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotOut)
async def get_snapshot(
    snapshot_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConfigSnapshot:
    return await _get_owned_snapshot(db, snapshot_id, user.org_id)


@router.get("/snapshots/{snapshot_id}/download")
async def download_snapshot(
    snapshot_id: UUID,
    ext: Literal["txt", "log"] = Query(default="txt"),
    include_timestamp: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PlainTextResponse:
    snapshot = await _get_owned_snapshot(db, snapshot_id, user.org_id)
    device = await db.get(Device, snapshot.device_id)
    filename = build_snapshot_filename(
        device.name if device else str(snapshot.device_id),
        collected_at=snapshot.collected_at,
        ext=ext,
        include_timestamp=include_timestamp,
    )
    return PlainTextResponse(
        content=snapshot.content,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _get_owned_device(db: AsyncSession, device_id: UUID, org_id: UUID) -> Device:
    device = await db.get(Device, device_id)
    if device is None or device.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


async def _get_owned_snapshot(db: AsyncSession, snapshot_id: UUID, org_id: UUID) -> ConfigSnapshot:
    snapshot = await db.get(ConfigSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")
    await _get_owned_device(db, snapshot.device_id, org_id)
    return snapshot
