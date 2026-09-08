from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.user import User
from app.services.device_types import DEVICE_TYPE_REGISTRY

router = APIRouter(prefix="/api/device-types", tags=["device-types"])


@router.get("")
async def list_device_types(user: User = Depends(get_current_user)) -> list[dict]:
    return [
        {
            "key": key,
            "label": spec.label,
            "category": spec.category,
            "requires_custom_command": not spec.default_commands,
            "default_commands": list(spec.default_commands),
        }
        for key, spec in DEVICE_TYPE_REGISTRY.items()
    ]
