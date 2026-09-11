import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.schemas.auth import CurrentUser, LoginRequest, RegisterRequest, TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    org = Organization(name=payload.org_name)
    db.add(org)
    await db.flush()

    user = User(
        org_id=org.id,
        email=payload.email,
        # bcrypt's work factor makes this genuinely slow (~100-300ms of pure
        # CPU) - off the event loop so it doesn't stall every other request
        # (including unrelated orgs' in-flight collection status polling)
        # for the duration.
        hashed_password=await asyncio.to_thread(hash_password, payload.password),
        role=UserRole.ADMIN,
    )
    db.add(user)
    await db.commit()

    token = create_access_token(user_id=user.id, org_id=org.id)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await db.scalar(select(User).where(User.email == payload.email))
    if user is None or not await asyncio.to_thread(verify_password, payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    token = create_access_token(user_id=user.id, org_id=user.org_id)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=CurrentUser)
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> CurrentUser:
    org = await db.get(Organization, user.org_id)
    return CurrentUser(
        id=user.id,
        org_id=user.org_id,
        org_name=org.name if org else "",
        email=user.email,
        role=user.role.value,
    )
