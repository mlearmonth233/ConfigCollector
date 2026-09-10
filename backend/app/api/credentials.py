from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.core.encryption import encrypt_secret
from app.database import get_db
from app.models.credential import Credential
from app.models.user import User
from app.schemas.credential import CredentialCreate, CredentialOut

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


def _to_out(c: Credential, fallback_name: str | None) -> CredentialOut:
    return CredentialOut(
        id=c.id,
        name=c.name,
        username=c.username,
        has_enable_secret=bool(c.encrypted_enable_secret),
        mfa_mode=c.mfa_mode,
        otp_delimiter=c.otp_delimiter,
        auth_timeout_seconds=c.auth_timeout_seconds,
        fallback_credential_id=c.fallback_credential_id,
        fallback_credential_name=fallback_name,
        is_default=c.is_default,
        created_at=c.created_at,
    )


@router.get("", response_model=list[CredentialOut])
async def list_credentials(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CredentialOut]:
    credentials = list(await db.scalars(select(Credential).where(Credential.org_id == user.org_id)))
    names_by_id = {c.id: c.name for c in credentials}
    return [_to_out(c, names_by_id.get(c.fallback_credential_id)) for c in credentials]


@router.post("", response_model=CredentialOut, status_code=status.HTTP_201_CREATED)
async def create_credential(
    payload: CredentialCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CredentialOut:
    fallback: Credential | None = None
    if payload.fallback_credential_id is not None:
        fallback = await db.get(Credential, payload.fallback_credential_id)
        if fallback is None or fallback.org_id != admin.org_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Fallback credential not found")
        if fallback.fallback_credential_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"'{fallback.name}' already has its own fallback credential - a credential can't "
                    "chain more than one level of fallback"
                ),
            )

    # The first credential an org ever adds becomes its default automatically
    # - devices don't need one picked per-device, so there should always be
    # an obvious answer for "which credential do untouched devices use" as
    # soon as there's anything to use at all.
    is_first_credential = (
        await db.scalar(select(Credential.id).where(Credential.org_id == admin.org_id).limit(1))
    ) is None

    credential = Credential(
        org_id=admin.org_id,
        name=payload.name,
        username=payload.username,
        encrypted_password=encrypt_secret(payload.password),
        encrypted_enable_secret=encrypt_secret(payload.enable_secret) if payload.enable_secret else None,
        mfa_mode=payload.mfa_mode,
        otp_delimiter=payload.otp_delimiter,
        auth_timeout_seconds=payload.auth_timeout_seconds,
        fallback_credential_id=payload.fallback_credential_id,
        is_default=is_first_credential,
    )
    db.add(credential)
    await db.commit()
    await db.refresh(credential)
    return _to_out(credential, fallback.name if fallback else None)


@router.post("/{credential_id}/set-default", response_model=CredentialOut)
async def set_default_credential(
    credential_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CredentialOut:
    """Makes this the org's one default credential - used for any device
    that doesn't have its own credential_id set. Only one credential per
    org is ever default; making a new one default un-defaults whichever
    one held it before."""
    credential = await db.get(Credential, credential_id)
    if credential is None or credential.org_id != admin.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")

    others = await db.scalars(
        select(Credential).where(Credential.org_id == admin.org_id, Credential.id != credential_id)
    )
    for other in others:
        if other.is_default:
            other.is_default = False
    credential.is_default = True
    await db.commit()
    await db.refresh(credential)

    fallback_name = None
    if credential.fallback_credential_id is not None:
        fallback = await db.get(Credential, credential.fallback_credential_id)
        fallback_name = fallback.name if fallback else None
    return _to_out(credential, fallback_name)


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    credential_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    credential = await db.get(Credential, credential_id)
    if credential is None or credential.org_id != admin.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")

    in_use_as_fallback = await db.scalar(
        select(Credential.id).where(Credential.fallback_credential_id == credential_id).limit(1)
    )
    if in_use_as_fallback is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This credential is set as another credential's fallback - remove that reference first",
        )

    await db.delete(credential)
    await db.commit()
