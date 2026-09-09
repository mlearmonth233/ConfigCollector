from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends

from app.api.command_profiles import get_org_command_overrides
from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.device_types import DEVICE_TYPE_REGISTRY, get_suggested_commands, parse_command_list

router = APIRouter(prefix="/api/device-types", tags=["device-types"])


@router.get("")
async def list_device_types(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    # default_commands reflects the org's saved Commands-page override when
    # one exists, falling back to the registry default otherwise - callers
    # here (the "add device" form's hint, the start-collection dialog's
    # per-run prefill) always want the org's actual current default.
    overrides = await get_org_command_overrides(db, user.org_id)
    result = []
    for key, spec in DEVICE_TYPE_REGISTRY.items():
        override = overrides.get(key)
        commands = parse_command_list(override.commands) if override else list(spec.default_commands)
        result.append(
            {
                "key": key,
                "label": spec.label,
                "category": spec.category,
                "requires_custom_command": not commands,
                "default_commands": commands,
                "is_custom_default": override is not None,
                "suggested_commands": list(get_suggested_commands(spec.category)),
            }
        )
    return result
