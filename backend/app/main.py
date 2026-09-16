import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.logging_config import configure_logging

# Before anything else imports so every module's logger has a file to go to.
configure_logging("api")
log = logging.getLogger("app")

from app.config import assert_secrets_configured  # noqa: E402

assert_secrets_configured()

from app.api import (  # noqa: E402
    alerts,
    auth,
    command_profiles,
    credentials,
    custom_device_types,
    device_roles,
    device_types,
    devices,
    dns_check,
    firmware,
    hostname_rules,
    jobs,
    logs,
    organizations,
    ping,
    schedules,
    snapshots,
    snmp,
    terminal,
    users,
)
from app.config import get_settings  # noqa: E402
from app.core.request_logging import install_request_logging  # noqa: E402
from app.database import init_db  # noqa: E402
from app.services.job_reaper import reap_orphaned_jobs  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("API starting up: initialising database")
    try:
        await init_db()
    except Exception:
        log.exception("Database initialisation failed - the API cannot start")
        raise
    settings = get_settings()
    if settings.celery_task_always_eager and settings.reap_jobs_on_start:
        # In eager mode this process *is* the worker, so a job still
        # marked running was interrupted when the API last stopped.
        try:
            await asyncio.to_thread(reap_orphaned_jobs, "the application was restarted")
        except Exception:
            log.exception("Start-up job cleanup failed")
    log.info("API ready")
    yield
    log.info("API shutting down")


app = FastAPI(title="Packrat", version="0.1.0", lifespan=lifespan)
install_request_logging(app)

app.add_middleware(
    CORSMiddleware,
    # Auth uses a Bearer token in the Authorization header, not cookies, so a
    # wildcard origin is safe here (no allow_credentials/cookie exposure).
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # Content-Disposition isn't on the browser's default CORS-safelisted
    # response headers, so without this, frontend JS can read the snapshot
    # download's *body* fine but silently gets an empty string back for
    # response.headers["content-disposition"] - the filename it carries
    # (hostname, extension, optional timestamp) would never be visible to
    # the page that's supposed to use it to name the saved file.
    expose_headers=["Content-Disposition"],
)

app.include_router(auth.router)
app.include_router(alerts.router)
app.include_router(users.router)
app.include_router(credentials.router)
app.include_router(devices.router)
app.include_router(device_types.router)
app.include_router(custom_device_types.router)
app.include_router(device_roles.router)
app.include_router(hostname_rules.router)
app.include_router(command_profiles.router)
app.include_router(jobs.router)
app.include_router(logs.router)
app.include_router(snapshots.router)
app.include_router(schedules.router)
app.include_router(organizations.router)
app.include_router(ping.router)
app.include_router(firmware.router)
app.include_router(dns_check.router)
app.include_router(snmp.router)
app.include_router(terminal.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
