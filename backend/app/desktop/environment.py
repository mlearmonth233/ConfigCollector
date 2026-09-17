"""Turns the data folder into the environment the backend expects.

The backend is configured entirely through environment variables (see
app/config.py). The desktop build derives every one of them from the data
folder, generates the two secrets on first run and remembers them in
packrat.env, and lets that same file carry any override the user wants
(LOG_LEVEL=DEBUG, PACKRAT_HOST=0.0.0.0 to reach it from other machines,
PACKRAT_PORT to move it off 8321).
"""

from __future__ import annotations

import os
import secrets
import stat
import sys
from pathlib import Path

from app.desktop.paths import DEFAULT_PORT, frontend_dist

ENV_FILE = "packrat.env"
DEVELOPMENT_ENVIRONMENTS = ("development", "dev", "test", "testing")


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = [
        "# Packrat desktop settings. Generated on first run; edit and restart Packrat to change.",
        "# JWT_SECRET_KEY and CREDENTIAL_ENCRYPTION_KEY protect logins and stored device",
        "# passwords: back this file up together with packrat.db, and never share it.",
        "# Optional extras: PACKRAT_HOST=0.0.0.0 (reachable from other machines),",
        "# PACKRAT_PORT=8321, LOG_LEVEL=DEBUG, DEVICE_CONNECT_TIMEOUT=20.",
    ]
    lines += [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name == "posix":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _sqlite_url(prefix: str, file: Path) -> str:
    # Three slashes then the path; on Windows the path keeps its drive
    # letter ("sqlite:///C:/Users/..."), which SQLAlchemy understands.
    return f"{prefix}:///{file.resolve().as_posix()}"


def prepare_environment(data: Path) -> dict[str, str]:
    """Creates the data folder layout, makes sure the secrets exist, and
    exports the backend's configuration into os.environ. Returns the values
    that were set."""
    data.mkdir(parents=True, exist_ok=True)
    (data / "logs").mkdir(exist_ok=True)
    (data / "firmware_images").mkdir(exist_ok=True)

    env_path = data / ENV_FILE
    stored = read_env_file(env_path)
    changed = False
    if not stored.get("JWT_SECRET_KEY"):
        stored["JWT_SECRET_KEY"] = secrets.token_urlsafe(48)
        changed = True
    if not stored.get("CREDENTIAL_ENCRYPTION_KEY"):
        from cryptography.fernet import Fernet  # noqa: PLC0415

        stored["CREDENTIAL_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
        changed = True
    if not stored.get("PACKRAT_PORT"):
        stored["PACKRAT_PORT"] = str(DEFAULT_PORT)
        changed = True
    if changed:
        write_env_file(env_path, stored)

    values: dict[str, str] = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": _sqlite_url("sqlite+aiosqlite", data / "packrat.db"),
        "CELERY_BROKER_URL": _sqlite_url("sqla+sqlite", data / "queue.db"),
        "CELERY_TASK_ALWAYS_EAGER": "false",
        "CELERY_BEAT_SCHEDULE_FILE": str(data / "celerybeat-schedule"),
        "LOG_DIR": str(data / "logs"),
        "FIRMWARE_STORAGE_DIR": str(data / "firmware_images"),
        "PACKRAT_HOST": "127.0.0.1",
        "PACKRAT_DATA_DIR": str(data),
    }
    dist = frontend_dist()
    if dist is not None:
        values["FRONTEND_DIST_DIR"] = str(dist)
    # The file's own entries win over the derived defaults, so a user can
    # move the database or the port without touching anything else.
    values.update(stored)
    # ...except the secrets must never be the placeholders, and a
    # development override of the tier is meaningless here.
    values.pop("LICENCE_OVERRIDE_TIER", None)
    if values["ENVIRONMENT"].lower() in DEVELOPMENT_ENVIRONMENTS:
        values["ENVIRONMENT"] = "production"

    os.environ.update(values)
    if sys.platform == "win32":
        # Celery's billiard needs this to behave on Windows even with the
        # thread pool; harmless elsewhere.
        os.environ.setdefault("FORKED_BY_MULTIPROCESSING", "1")
    return values


def prepare_broker(url: str) -> None:
    """Creates the queue's tables once, before the API, worker and beat all
    start and would otherwise race each other to do it."""
    if not url.startswith(("sqla+", "sqlalchemy+")):
        return
    from kombu import Connection  # noqa: PLC0415

    with Connection(url) as connection:
        channel = connection.default_channel
        # Declaring the queue the worker consumes creates kombu's tables.
        channel.queue_declare(queue="celery")
