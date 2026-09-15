from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.hostname_rules import load_rules
from app.database import get_db
from app.models.user import User
from app.services.hostname_detection import roles_for_rules

router = APIRouter(prefix="/api/device-roles", tags=["device-roles"])


@router.get("")
async def list_device_roles(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Built-in roles plus any role the org's naming rules introduce."""
    roles = roles_for_rules(await load_rules(db, user.org_id))
    return [{"key": key, "label": label} for key, label in roles.items()]
