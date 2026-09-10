from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.user import User
from app.services.hostname_detection import DEVICE_ROLES

router = APIRouter(prefix="/api/device-roles", tags=["device-roles"])


@router.get("")
async def list_device_roles(user: User = Depends(get_current_user)) -> list[dict]:
    return [{"key": key, "label": label} for key, label in DEVICE_ROLES.items()]
