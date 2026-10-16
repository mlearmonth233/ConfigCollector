"""Log history: the saved log file of every collection, file push and
Wireless Audit run (services/log_archive.py) - list, read, download, and
for admins delete or clear old ones. Each organization sees its own."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, require_admin
from app.config import get_settings
from app.models.user import User
from app.services import log_archive as la

router = APIRouter(prefix="/api/log-history", tags=["log-history"])

MAX_VIEW_BYTES = 2_000_000


class LogEntryOut(BaseModel):
    path: str
    day: str
    kind: str
    name: str
    size_bytes: int
    modified_at: datetime


class LogHistoryOut(BaseModel):
    directory: str
    retention_days: int
    total_files: int
    total_bytes: int
    files: list[LogEntryOut]


class LogFileOut(BaseModel):
    path: str
    size_bytes: int
    truncated: bool
    content: str


class PathsIn(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=5000)


class PurgeIn(BaseModel):
    older_than_days: int = Field(ge=0, le=3650)


class DeletedOut(BaseModel):
    deleted: int


@router.get("", response_model=LogHistoryOut)
async def list_history(
    kind: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    user: User = Depends(get_current_user),
) -> LogHistoryOut:
    every = la.entries(user.org_id)
    files = every
    if kind:
        files = [e for e in files if e.kind == kind]
    if q and q.strip():
        needle = q.strip().lower()
        files = [e for e in files if needle in e.name.lower() or needle in e.day]
    return LogHistoryOut(
        directory=str(la.org_dir(user.org_id)),
        retention_days=get_settings().log_archive_retention_days,
        total_files=len(every),
        total_bytes=sum(e.size_bytes for e in every),
        files=[LogEntryOut(**e.__dict__) for e in files],
    )


def _file(user: User, path: str):
    found = la.resolve(user.org_id, path)
    if found is None:
        raise HTTPException(status_code=404, detail="No such log file")
    return found


@router.get("/file", response_model=LogFileOut)
async def read_file(path: str = Query(...), user: User = Depends(get_current_user)) -> LogFileOut:
    found = _file(user, path)
    size = found.stat().st_size
    with found.open("rb") as fh:
        data = fh.read(MAX_VIEW_BYTES)
    return LogFileOut(path=path, size_bytes=size, truncated=size > MAX_VIEW_BYTES, content=data.decode("utf-8", errors="replace"))


@router.get("/download")
async def download_file(path: str = Query(...), user: User = Depends(get_current_user)) -> Response:
    found = _file(user, path)
    return Response(
        found.read_bytes(), media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{found.name}"'}
    )


@router.post("/download.zip")
async def download_zip(body: PathsIn, user: User = Depends(get_current_user)) -> Response:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in body.paths:
            found = la.resolve(user.org_id, path)
            if found is not None:
                zf.write(found, arcname=path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="packrat-logs-{stamp}.zip"'})


@router.post("/delete", response_model=DeletedOut)
async def delete_files(body: PathsIn, admin: User = Depends(require_admin)) -> DeletedOut:
    return DeletedOut(deleted=la.delete(admin.org_id, body.paths))


@router.post("/purge", response_model=DeletedOut)
async def purge_files(body: PurgeIn, admin: User = Depends(require_admin)) -> DeletedOut:
    """Delete every day older than older_than_days (0: everything)."""
    return DeletedOut(deleted=la.purge(admin.org_id, body.older_than_days))
