"""Where the API and the worker ask "what is this organization allowed to
do?". Everything funnels through core/licence.py's tier table."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.licence import LicenceInfo, development_override, licence_from_key, upgrade_message
from app.models.device import Device
from app.models.licence import Licence
from app.models.user import User

# 402 is the closest status the web has for "pay to unlock"; the frontend
# turns it into an upgrade notice rather than a generic error.
PAYMENT_REQUIRED = status.HTTP_402_PAYMENT_REQUIRED


async def get_licence(db: AsyncSession, org_id) -> LicenceInfo:
    override = development_override()
    if override is not None:
        return override
    row = await db.scalar(select(Licence).where(Licence.org_id == org_id))
    return licence_from_key(row.key if row else None)


def get_licence_sync(db, org_id) -> LicenceInfo:
    """Same, for Celery tasks using the sync session."""
    override = development_override()
    if override is not None:
        return override
    row = db.query(Licence).filter(Licence.org_id == org_id).first()
    return licence_from_key(row.key if row else None)


async def require_feature(db: AsyncSession, org_id, feature: str) -> LicenceInfo:
    info = await get_licence(db, org_id)
    if not info.has(feature):
        raise HTTPException(status_code=PAYMENT_REQUIRED, detail=upgrade_message(feature, info))
    return info


async def check_device_limit(db: AsyncSession, org_id, adding: int = 1) -> None:
    info = await get_licence(db, org_id)
    if info.max_devices is None:
        return
    count = await db.scalar(select(func.count()).select_from(Device).where(Device.org_id == org_id)) or 0
    if count + adding > info.max_devices:
        raise HTTPException(
            status_code=PAYMENT_REQUIRED,
            detail=(
                f"{info.tier.label} allows up to {info.max_devices} devices and this organization has {count}"
                f"{' (adding ' + str(adding) + ' would exceed it)' if adding > 1 else ''}. "
                "Enter a licence key under Settings > Licence to add more."
            ),
        )


async def check_user_limit(db: AsyncSession, org_id) -> None:
    info = await get_licence(db, org_id)
    if info.tier.max_users is None:
        return
    count = await db.scalar(select(func.count()).select_from(User).where(User.org_id == org_id)) or 0
    if count + 1 > info.tier.max_users:
        raise HTTPException(status_code=PAYMENT_REQUIRED, detail=upgrade_message("multi_user", info))
