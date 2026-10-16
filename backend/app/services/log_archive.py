"""Log history: every collection, file push and Wireless Audit run is also
saved as a plain text file, so it can be found and read back later from the
Log history page (api/log_archive.py) or straight from disk.

Layout: <log_archive_dir>/<org id>/<YYYY-MM-DD>/<HHMMSS>_<kind>_<name>.log
Writing never raises: a full disk or a permissions problem must not fail
the collection it describes, so it is logged and skipped.
"""

from __future__ import annotations

import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)

KINDS = ("collection", "firmware", "audit")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FILE = re.compile(r"^(?P<time>\d{6})_(?P<kind>[a-z]+)_(?P<name>.+)\.log$")


def root() -> Path:
    return Path(get_settings().log_archive_dir).expanduser().resolve()


def org_dir(org_id) -> Path:
    return root() / str(uuid.UUID(str(org_id)))


def _safe(text: str, limit: int = 80) -> str:
    return (_SAFE.sub("-", text).strip("-.") or "log")[:limit]


def save(org_id, kind: str, name: str, text: str, when: datetime | None = None) -> Path | None:
    """Write one log file; returns its path (None if it could not be written)."""
    try:
        when = (when or datetime.now(timezone.utc)).astimezone()
        folder = org_dir(org_id) / when.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{when.strftime('%H%M%S')}_{kind}_{_safe(name)}"
        path = folder / f"{stem}.log"
        n = 1
        while path.exists():
            n += 1
            path = folder / f"{stem}-{n}.log"
        path.write_text(text, encoding="utf-8", errors="replace")
        return path
    except Exception:  # noqa: BLE001
        log.exception("Could not save the %s log for %s to the log history", kind, name)
        return None


@dataclass
class Entry:
    path: str  # "<date>/<file>", relative to the org's folder
    day: str
    kind: str
    name: str
    size_bytes: int
    modified_at: datetime


def entries(org_id) -> list[Entry]:
    """Newest first."""
    base = org_dir(org_id)
    if not base.is_dir():
        return []
    out: list[Entry] = []
    for day in base.iterdir():
        if not day.is_dir() or not _DAY.match(day.name):
            continue
        for f in day.iterdir():
            if not f.is_file():
                continue
            m = _FILE.match(f.name)
            st = f.stat()
            out.append(
                Entry(
                    path=f"{day.name}/{f.name}",
                    day=day.name,
                    kind=m.group("kind") if m else "other",
                    name=m.group("name") if m else f.stem,
                    size_bytes=st.st_size,
                    modified_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
                )
            )
    out.sort(key=lambda e: (e.day, e.path), reverse=True)
    return out


def resolve(org_id, rel: str) -> Path | None:
    """An org's file by its "<date>/<file>" path - nothing outside it."""
    parts = rel.replace("\\", "/").split("/")
    if len(parts) != 2 or not _DAY.match(parts[0]) or parts[1].startswith(".") or not parts[1]:
        return None
    path = org_dir(org_id) / parts[0] / parts[1]
    try:
        path.resolve().relative_to(org_dir(org_id).resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def _drop_empty_days(org_id) -> None:
    base = org_dir(org_id)
    if base.is_dir():
        for day in base.iterdir():
            if day.is_dir() and not any(day.iterdir()):
                day.rmdir()


def delete(org_id, rels: list[str]) -> int:
    n = 0
    for rel in rels:
        path = resolve(org_id, rel)
        if path is not None:
            path.unlink(missing_ok=True)
            n += 1
    _drop_empty_days(org_id)
    return n


def purge(org_id, older_than_days: int, today: date | None = None) -> int:
    """Delete whole days older than the given number of days (0: everything)."""
    base = org_dir(org_id)
    if not base.is_dir():
        return 0
    cutoff = (today or date.today()) - timedelta(days=older_than_days)
    n = 0
    for day in list(base.iterdir()):
        if not day.is_dir() or not _DAY.match(day.name):
            continue
        if older_than_days == 0 or date.fromisoformat(day.name) < cutoff:
            n += sum(1 for f in day.iterdir() if f.is_file())
            shutil.rmtree(day, ignore_errors=True)
    return n


def purge_expired(today: date | None = None) -> int:
    """Apply log_archive_retention_days to every organization's files."""
    days = get_settings().log_archive_retention_days
    base = root()
    if days <= 0 or not base.is_dir():
        return 0
    n = 0
    for org in base.iterdir():
        try:
            n += purge(uuid.UUID(org.name), days, today)
        except ValueError:
            continue
    if n:
        log.info("Log history: removed %d file(s) older than %d days", n, days)
    return n


# --- what goes in the files -------------------------------------------------------------------------


def _ts(value: datetime | None) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") if value else "-"


def device_run_text(
    *,
    title: str,
    job_name: str | None,
    job_id,
    device_name: str,
    host: str,
    device_type: str,
    status: str,
    started_at: datetime | None,
    finished_at: datetime | None,
    error: str | None,
    extra: list[tuple[str, str]] | None = None,
    transcript: str | None,
    content: str | None = None,
) -> str:
    lines = [
        f"Packrat {title}",
        f"Job:       {job_name or '-'} ({job_id})",
        f"Device:    {device_name} ({host}, {device_type})",
        f"Status:    {status}",
        f"Started:   {_ts(started_at)}",
        f"Finished:  {_ts(finished_at)}",
    ]
    for key, value in extra or []:
        lines.append(f"{key + ':':<10} {value}")
    if error:
        lines.append(f"Error:     {error}")
    out = "\n".join(lines) + "\n"
    out += "\n===== Session log =====\n" + (transcript or "(nothing recorded)\n")
    if content:
        out += "\n\n===== Collected output =====\n" + content
    return out if out.endswith("\n") else out + "\n"
