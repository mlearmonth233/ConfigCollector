import difflib
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.device import Device
from app.models.job import CollectionJob, CollectionJobItem
from app.models.snapshot import ConfigSnapshot
from app.models.user import User
from app.schemas.snapshot import SnapshotDiffOut, SnapshotOut, SnapshotSummaryOut
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


@router.get("/snapshots/diff", response_model=SnapshotDiffOut)
async def diff_snapshots(
    from_id: UUID,
    to_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SnapshotDiffOut:
    """A unified diff between two snapshots - normally two collections of
    the same device, showing what changed in its config over time, but
    nothing here actually requires that (comparing across devices is
    allowed too, e.g. checking a new switch's config against a known-good
    template). Registered before the "/{snapshot_id}" route below so
    "diff" doesn't get swallowed as a snapshot id."""
    from_snapshot = await _get_owned_snapshot(db, from_id, user.org_id)
    to_snapshot = await _get_owned_snapshot(db, to_id, user.org_id)

    diff_lines = list(
        difflib.unified_diff(
            from_snapshot.content.splitlines(),
            to_snapshot.content.splitlines(),
            fromfile=f"{from_snapshot.collected_at.isoformat()}",
            tofile=f"{to_snapshot.collected_at.isoformat()}",
            lineterm="",
        )
    )
    return SnapshotDiffOut(
        from_id=from_snapshot.id,
        to_id=to_snapshot.id,
        from_collected_at=from_snapshot.collected_at,
        to_collected_at=to_snapshot.collected_at,
        diff=diff_lines,
    )


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
    device = await db.get(Device, snapshot.device_id) if snapshot.device_id else None
    filename = build_snapshot_filename(
        device.name if device else "deleted-device",
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
    # Ownership is checked via the job that collected this snapshot, not via
    # its device - a snapshot must stay viewable/downloadable even after its
    # device is later deleted (device_id gets nulled, not the snapshot).
    job_org_id = await db.scalar(
        select(CollectionJob.org_id)
        .join(CollectionJobItem, CollectionJobItem.job_id == CollectionJob.id)
        .where(CollectionJobItem.id == snapshot.job_item_id)
    )
    if job_org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")
    return snapshot
