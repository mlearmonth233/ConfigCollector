"""Settings > Troubleshooting: view and download the application's log
files, and receive errors the browser side of the app ran into.

Admin-only. The files are server-wide (every process, every organisation
on this server), so a hosted multi-tenant install sets
LOG_DOWNLOAD_ENABLED=false and hands logs out from the server instead.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, require_admin
from app.config import get_settings
from app.core.logging_config import list_log_files, log_dir, system_info, tail_file
from app.models.user import User

router = APIRouter(prefix="/api/logs", tags=["logs"])
log = logging.getLogger("app.logs")
client_log = logging.getLogger("app.browser")


class LogFileOut(BaseModel):
    name: str
    size_bytes: int
    modified_at: datetime


class LogTailOut(BaseModel):
    name: str
    lines: int
    content: str


class LogOverviewOut(BaseModel):
    enabled: bool
    directory: str
    level: str
    files: list[LogFileOut]


class ClientErrorIn(BaseModel):
    message: str = Field(max_length=2000)
    source: str | None = Field(default=None, max_length=500)  # page URL
    stack: str | None = Field(default=None, max_length=8000)
    kind: str = Field(default="error", max_length=40)  # error | unhandledrejection | render


def _guard() -> None:
    if not get_settings().log_download_enabled:
        raise HTTPException(status_code=403, detail="Log access from the app is disabled on this server (LOG_DOWNLOAD_ENABLED=false)")


def _resolve(name: str) -> Path:
    """Only files that actually are in the log directory - no path tricks."""
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(status_code=404, detail="No such log file")
    path = log_dir() / name
    if not path.is_file() or path not in list_log_files():
        raise HTTPException(status_code=404, detail="No such log file")
    return path


@router.get("", response_model=LogOverviewOut)
async def overview(_: User = Depends(require_admin)) -> LogOverviewOut:
    settings = get_settings()
    files = []
    if settings.log_download_enabled:
        for path in list_log_files():
            stat = path.stat()
            files.append(LogFileOut(name=path.name, size_bytes=stat.st_size, modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)))
    return LogOverviewOut(enabled=settings.log_download_enabled, directory=str(log_dir()), level=settings.log_level.upper(), files=files)


@router.get("/tail", response_model=LogTailOut)
async def tail(
    name: str = Query(..., description="A file name from the overview"),
    lines: int = Query(200, ge=10, le=5000),
    _: User = Depends(require_admin),
) -> LogTailOut:
    _guard()
    path = _resolve(name)
    content = tail_file(path, lines)
    return LogTailOut(name=path.name, lines=len(content.splitlines()), content=content)


@router.get("/download")
async def download(user: User = Depends(require_admin)) -> StreamingResponse:
    """Every log file plus a system-info.txt, zipped, for sending to
    whoever is troubleshooting."""
    _guard()
    log.info("Log bundle downloaded by user %s", user.id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("system-info.txt", system_info())
        for path in list_log_files():
            archive.write(path, arcname=path.name)
    buffer.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"packrat-logs-{stamp}.zip"
    return StreamingResponse(buffer, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/client", status_code=204, response_class=Response)
async def record_client_error(payload: ClientErrorIn, request: Request, user: User = Depends(get_current_user)) -> Response:
    """The frontend posts uncaught JavaScript errors here so a problem that
    only shows in the browser still ends up in packrat-api.log next to the
    server-side events of the same moment."""
    stack = f"\n{payload.stack.strip()}" if payload.stack else ""
    client_log.error(
        "Browser %s on %s (user %s, %s): %s%s",
        payload.kind,
        payload.source or "unknown page",
        user.id,
        request.headers.get("user-agent", "unknown browser")[:200],
        payload.message,
        stack,
    )
    return Response(status_code=204)
