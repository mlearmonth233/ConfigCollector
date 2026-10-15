"""Create (or reset) an administrator from the command line, for a fresh
install or a forgotten password - nothing is created unless this is run:

    cd backend
    python -m app.create_admin admin 'S0me-Password' --org "My Org"

The login may be an email address or a plain username ("admin"). An
existing user with that login gets the new password and the admin role; a
new one gets its own organization (--org, default "Packrat").
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

import app.models  # noqa: F401 - every mapper registered
from app.core.security import hash_password
from app.database import async_session_factory, init_db
from app.models.organization import Organization
from app.models.user import User, UserRole


async def create_admin(login: str, password: str, org_name: str) -> str:
    await init_db()
    async with async_session_factory() as db:
        user = await db.scalar(select(User).where(User.email == login))
        if user is not None:
            user.hashed_password = hash_password(password)
            user.role = UserRole.ADMIN
            await db.commit()
            return f"Reset the password of {login} and made it an admin."
        org = Organization(name=org_name)
        db.add(org)
        await db.flush()
        db.add(User(org_id=org.id, email=login, hashed_password=hash_password(password), role=UserRole.ADMIN))
        await db.commit()
        return f"Created admin {login} in a new organization '{org_name}'."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.create_admin", description="Create or reset a Packrat administrator.")
    parser.add_argument("login", help="email address or username, e.g. admin")
    parser.add_argument("password", help="at least 8 characters")
    parser.add_argument("--org", default="Packrat", help="organization name for a new admin (default: Packrat)")
    args = parser.parse_args(argv)
    if len(args.password) < 8:
        parser.error("the password must be at least 8 characters")
    print(asyncio.run(create_admin(args.login.strip(), args.password, args.org)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
