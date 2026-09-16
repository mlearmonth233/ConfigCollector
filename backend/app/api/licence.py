"""Settings > Licence: see the tier in effect, apply or remove a key."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.core.licence import FEATURE_LABELS, InvalidLicence, LicenceInfo, licence_from_key, parse_key
from app.database import get_db
from app.models.device import Device
from app.models.licence import Licence
from app.models.user import User
from app.schemas.licence import LicenceApply, LicenceOut
from app.services.licensing import get_licence

router = APIRouter(prefix="/api/licence", tags=["licence"])


async def _out(db: AsyncSession, org_id, info: LicenceInfo, has_key: bool) -> LicenceOut:
    devices = await db.scalar(select(func.count()).select_from(Device).where(Device.org_id == org_id)) or 0
    users = await db.scalar(select(func.count()).select_from(User).where(User.org_id == org_id)) or 0
    return LicenceOut(
        tier=info.tier.key,
        tier_label=info.tier.label,
        valid=info.valid,
        key_id=info.key_id,
        customer=info.customer,
        issued_at=info.issued_at,
        expires_at=info.expires_at,
        problem=info.problem,
        features=sorted(info.features),
        all_features=FEATURE_LABELS,
        max_devices=info.max_devices,
        device_count=int(devices),
        max_users=info.tier.max_users,
        user_count=int(users),
        max_retention_days=info.tier.max_retention_days,
        has_key=has_key,
    )


@router.get("", response_model=LicenceOut)
async def get_current_licence(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> LicenceOut:
    info = await get_licence(db, user.org_id)
    has_key = (await db.scalar(select(Licence.id).where(Licence.org_id == user.org_id))) is not None
    return await _out(db, user.org_id, info, has_key)


@router.put("", response_model=LicenceOut)
async def apply_licence(payload: LicenceApply, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> LicenceOut:
    key = payload.key.strip()
    try:
        parse_key(key)
    except InvalidLicence as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    info = licence_from_key(key)
    if not info.valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=info.problem or "This licence key is not valid")
    row = await db.scalar(select(Licence).where(Licence.org_id == admin.org_id))
    if row is None:
        row = Licence(org_id=admin.org_id)
        db.add(row)
    row.key = key
    row.applied_by_id = admin.id
    row.applied_at = datetime.now(timezone.utc)
    await db.commit()
    return await _out(db, admin.org_id, await get_licence(db, admin.org_id), True)


@router.delete("", response_model=LicenceOut)
async def remove_licence(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> LicenceOut:
    row = await db.scalar(select(Licence).where(Licence.org_id == admin.org_id))
    if row is not None:
        await db.delete(row)
        await db.commit()
    return await _out(db, admin.org_id, await get_licence(db, admin.org_id), False)
