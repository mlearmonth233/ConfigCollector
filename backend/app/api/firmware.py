import os
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.firmware import ACTIVE_FIRMWARE_JOB_STATUSES, FirmwareImage, FirmwareJob, FirmwareJobItem
from app.models.user import User
from app.schemas.firmware import FirmwareImageOut, FirmwareImageUpdate
from app.services import firmware_storage

router = APIRouter(prefix="/api/firmware", tags=["firmware"])


@router.get("", response_model=list[FirmwareImageOut])
async def list_firmware_images(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[FirmwareImage]:
    result = await db.scalars(
        select(FirmwareImage).where(FirmwareImage.org_id == user.org_id).order_by(FirmwareImage.created_at.desc())
    )
    return list(result)


@router.post("", response_model=FirmwareImageOut, status_code=status.HTTP_201_CREATED)
async def upload_firmware_image(
    file: UploadFile = File(...),
    description: str | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FirmwareImage:
    """Streams the upload straight to disk (see services/firmware_storage.py)
    while hashing it - firmware images routinely run several hundred MB to
    over a gigabyte, so this never buffers the whole file in memory."""
    stored = await firmware_storage.save_upload(user.org_id, file)
    if stored.size_bytes == 0:
        firmware_storage.delete_file(stored.storage_path)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    image = FirmwareImage(
        org_id=user.org_id,
        uploaded_by_id=user.id,
        filename=os.path.basename(file.filename or "firmware.bin"),
        description=description,
        size_bytes=stored.size_bytes,
        md5=stored.md5,
        storage_path=stored.storage_path,
    )
    db.add(image)
    await db.commit()
    await db.refresh(image)
    return image


@router.patch("/{image_id}", response_model=FirmwareImageOut)
async def update_firmware_image(
    image_id: UUID,
    payload: FirmwareImageUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FirmwareImage:
    image = await _get_owned_image(db, image_id, user.org_id)
    image.description = payload.description
    await db.commit()
    await db.refresh(image)
    return image


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_firmware_image(
    image_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    image = await _get_owned_image(db, image_id, user.org_id)

    in_progress = await db.scalar(
        select(FirmwareJobItem.id)
        .join(FirmwareJob, FirmwareJob.id == FirmwareJobItem.job_id)
        .where(
            FirmwareJob.firmware_image_id == image_id,
            FirmwareJobItem.status.in_(ACTIVE_FIRMWARE_JOB_STATUSES),
        )
        .limit(1)
    )
    if in_progress is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This firmware image has a push in progress - wait for it to finish (or cancel it) "
                "before deleting"
            ),
        )

    # Past jobs keep their own copy of everything they need (filename, md5,
    # size, storage path - see FirmwareJob's denormalized fields) so they
    # survive the image itself being deleted, same as a device outliving its
    # deleted config snapshots' job history.
    firmware_storage.delete_file(image.storage_path)
    await db.delete(image)
    await db.commit()


async def _get_owned_image(db: AsyncSession, image_id: UUID, org_id) -> FirmwareImage:
    image = await db.get(FirmwareImage, image_id)
    if image is None or image.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firmware image not found")
    return image
