from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User
from app.core.licence import effective_retention_days
from app.schemas.organization import OrganizationSettingsOut, OrganizationSettingsUpdate
from app.services.licensing import get_licence

router = APIRouter(prefix="/api/organization", tags=["organization"])


@router.get("", response_model=OrganizationSettingsOut)
async def get_organization_settings(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrganizationSettingsOut:
    org = await db.get(Organization, user.org_id)
    return OrganizationSettingsOut(name=org.name, snapshot_retention_days=org.snapshot_retention_days)


@router.patch("", response_model=OrganizationSettingsOut)
async def update_organization_settings(
    payload: OrganizationSettingsUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> OrganizationSettingsOut:
    org = await db.get(Organization, admin.org_id)
    if payload.snapshot_retention_days is not None:
        org.snapshot_retention_days = payload.snapshot_retention_days
    elif payload.clear_retention:
        org.snapshot_retention_days = None
    # The free tier keeps history for a limited time whatever is asked for.
    org.snapshot_retention_days = effective_retention_days(org.snapshot_retention_days, await get_licence(db, admin.org_id))
    await db.commit()
    await db.refresh(org)
    return OrganizationSettingsOut(name=org.name, snapshot_retention_days=org.snapshot_retention_days)
