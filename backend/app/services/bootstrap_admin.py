"""A known admin login on this install, from BOOTSTRAP_ADMIN_EMAIL and
BOOTSTRAP_ADMIN_PASSWORD (app/config.py).

At every API start-up: a new email gets its own organization and an admin
account; an existing account gets that password and the admin role. That
makes it both a "default admin" for a fresh install and a way back in when
the password is forgotten. The values live only in the machine's own .env
(or the desktop app's packrat.env), so nothing secret is in the code.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.config import get_settings
from app.core.security import hash_password, verify_password
from app.models.organization import Organization
from app.models.user import User, UserRole

log = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 8  # the same minimum the Register page enforces


def ensure_bootstrap_admin(session_factory) -> str | None:
    """Returns what was done ("created", "updated", "unchanged"), or None
    when no bootstrap admin is configured or the settings are unusable."""
    settings = get_settings()
    email = settings.bootstrap_admin_email.strip()
    password = settings.bootstrap_admin_password
    if not email and not password:
        return None
    if not email or not password:
        log.warning("Bootstrap admin skipped: set both BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD")
        return None
    if "@" not in email:
        log.warning("Bootstrap admin skipped: BOOTSTRAP_ADMIN_EMAIL %r is not an email address", email)
        return None
    if len(password) < MIN_PASSWORD_LENGTH:
        log.warning("Bootstrap admin skipped: BOOTSTRAP_ADMIN_PASSWORD must be at least %d characters", MIN_PASSWORD_LENGTH)
        return None

    db = session_factory()
    try:
        user = db.scalar(select(User).where(func.lower(User.email) == email.lower()))
        if user is None:
            org = Organization(name=settings.bootstrap_admin_org_name.strip() or "Packrat")
            db.add(org)
            db.flush()
            db.add(User(org_id=org.id, email=email, hashed_password=hash_password(password), role=UserRole.ADMIN))
            db.commit()
            log.info("Bootstrap admin %s created, in a new organization %r", email, org.name)
            return "created"

        changes = []
        if not verify_password(password, user.hashed_password):
            user.hashed_password = hash_password(password)
            changes.append("password set")
        if user.role != UserRole.ADMIN:
            user.role = UserRole.ADMIN
            changes.append("made an admin")
        if not changes:
            return "unchanged"
        db.commit()
        log.info("Bootstrap admin %s updated: %s", user.email, ", ".join(changes))
        return "updated"
    finally:
        db.close()
