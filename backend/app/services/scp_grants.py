"""Ephemeral, single-use SCP grants.

A firmware push job (services/firmware_push.py, run inside a Celery worker)
mints one of these per device right before telling it to "copy scp://...",
then the SCP server (services/scp_server.py, run as its own long-lived
process - see app/scp_server_main.py) is what actually authenticates the
device and serves the file. Those are two separate OS processes, so the
grant has to live somewhere both can reach it - Redis (already used as the
Celery broker) is that shared store.

A grant is a random, single-purpose username/password pair good for
downloading exactly one file, expiring on its own after a bounded TTL even
if never claimed. Redis is treated as trusted internal infrastructure here,
same as the one-time-passcodes that already transit it as Celery task
arguments (see README's security notes) - nothing here is exposed outside
the backend/worker/scp-server processes.
"""

import json
import secrets
from dataclasses import dataclass

import redis

from app.config import get_settings

settings = get_settings()
_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)

_KEY_PREFIX = "scp_grant:"


@dataclass(frozen=True)
class ScpGrant:
    username: str
    password: str
    file_path: str  # absolute path on disk of the file to serve
    remote_filename: str  # basename the device's "copy scp://.../<this>" must request


def create_grant(*, file_path: str, remote_filename: str, ttl_seconds: int | None = None) -> ScpGrant:
    """Mints a brand-new username/password pair, valid until either
    invalidate_grant() is called (see scp_server.py, once the device's
    download attempt has run to completion) or `ttl_seconds` elapses,
    whichever comes first."""
    username = secrets.token_urlsafe(12)
    password = secrets.token_urlsafe(24)
    ttl = ttl_seconds if ttl_seconds is not None else settings.scp_grant_ttl_seconds
    _redis.set(
        _KEY_PREFIX + username,
        json.dumps({"password": password, "file_path": file_path, "remote_filename": remote_filename}),
        ex=ttl,
    )
    return ScpGrant(username=username, password=password, file_path=file_path, remote_filename=remote_filename)


def peek_grant(username: str) -> ScpGrant | None:
    """Read-only lookup used to check a password during SSH authentication -
    deliberately doesn't consume the grant, since a client may probe
    authentication more than once within a single connection attempt before
    actually requesting the file."""
    raw = _redis.get(_KEY_PREFIX + username)
    if raw is None:
        return None
    data = json.loads(raw)
    return ScpGrant(
        username=username,
        password=data["password"],
        file_path=data["file_path"],
        remote_filename=data["remote_filename"],
    )


def invalidate_grant(username: str) -> None:
    """Consumes (or explicitly revokes) a grant so it can never be reused -
    called once the SCP server has finished handling the one exec request a
    grant is good for, whatever the outcome, and also from firmware_push.py
    if a device's copy command is never even attempted (e.g. the job was
    cancelled, or authentication to the device itself failed first)."""
    _redis.delete(_KEY_PREFIX + username)
