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


def _to_out(c: Credential) -> CredentialOut:
    return CredentialOut(
        id=c.id,
        name=c.name,
        username=c.username,
        has_enable_secret=bool(c.encrypted_enable_secret),
        created_at=c.created_at,
    )


@router.get("", response_model=list[CredentialOut])
async def list_credentials(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CredentialOut]:
    result = await db.scalars(select(Credential).where(Credential.org_id == user.org_id))
    return [_to_out(c) for c in result]


@router.post("", response_model=CredentialOut, status_code=status.HTTP_201_CREATED)
async def create_credential(
    payload: CredentialCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CredentialOut:
    credential = Credential(
        org_id=admin.org_id,
        name=payload.name,
        username=payload.username,
        encrypted_password=encrypt_secret(payload.password),
        encrypted_enable_secret=encrypt_secret(payload.enable_secret) if payload.enable_secret else None,
    )
    db.add(credential)
    await db.commit()
    await db.refresh(credential)
    return _to_out(credential)


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    credential_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    credential = await db.get(Credential, credential_id)
    if credential is None or credential.org_id != admin.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
    await db.delete(credential)
    await db.commit()
