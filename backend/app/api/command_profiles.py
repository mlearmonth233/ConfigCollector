from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, require_admin
from app.database import get_db
from app.models.command_profile import CommandProfile
from app.models.user import User
from app.schemas.command_profile import CommandProfileOut, CommandProfileUpdate
from app.services.device_types import DEVICE_TYPE_REGISTRY, get_device_type_spec, parse_command_list

router = APIRouter(prefix="/api/command-profiles", tags=["command-profiles"])


async def get_org_command_overrides(db: AsyncSession, org_id) -> dict[str, CommandProfile]:
    """Every CommandProfile row an org has saved, keyed by device_type -
    shared with the /api/device-types endpoint, which merges these into its
    response so callers always see an org's actual current default."""
    rows = await db.scalars(select(CommandProfile).where(CommandProfile.org_id == org_id))
    return {row.device_type: row for row in rows}


def _to_out(device_type: str, override: CommandProfile | None) -> CommandProfileOut:
    spec = get_device_type_spec(device_type)
    commands = parse_command_list(override.commands) if override else list(spec.default_commands)
    return CommandProfileOut(
        device_type=device_type,
        label=spec.label,
        category=spec.category,
        commands=commands,
        is_custom=override is not None,
    )


@router.get("", response_model=list[CommandProfileOut])
async def list_command_profiles(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CommandProfileOut]:
    overrides = await get_org_command_overrides(db, user.org_id)
    return [_to_out(key, overrides.get(key)) for key in DEVICE_TYPE_REGISTRY]


@router.put("/{device_type}", response_model=CommandProfileOut)
async def upsert_command_profile(
    device_type: str,
    payload: CommandProfileUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CommandProfileOut:
    if device_type not in DEVICE_TYPE_REGISTRY:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown device type")

    existing = await db.scalar(
        select(CommandProfile).where(
            CommandProfile.org_id == admin.org_id, CommandProfile.device_type == device_type
        )
    )
    commands_str = ", ".join(payload.commands)
    if existing is None:
        existing = CommandProfile(org_id=admin.org_id, device_type=device_type, commands=commands_str)
        db.add(existing)
    else:
        existing.commands = commands_str
    await db.commit()
    return _to_out(device_type, existing)


@router.delete("/{device_type}", response_model=CommandProfileOut)
async def reset_command_profile(
    device_type: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CommandProfileOut:
    if device_type not in DEVICE_TYPE_REGISTRY:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown device type")

    existing = await db.scalar(
        select(CommandProfile).where(
            CommandProfile.org_id == admin.org_id, CommandProfile.device_type == device_type
        )
    )
    if existing is not None:
        await db.delete(existing)
        await db.commit()
    return _to_out(device_type, None)
