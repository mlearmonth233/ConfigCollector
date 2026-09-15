"""Application-wide file logging.

Every Packrat process (API server, Celery worker, Celery beat) writes to its
own rotating log file under LOG_DIR (default ./logs, next to the database):

    logs/packrat-api.log      the web/API server: every request, every
                              error with its traceback, browser-side errors
                              reported by the frontend
    logs/packrat-worker.log   the Celery worker: every collection, push,
                              DNS, SNMP and monitoring task, with the
                              per-device outcome and any traceback
    logs/packrat-beat.log     Celery beat: which periodic tasks it fired

Each file rotates at LOG_MAX_BYTES (10 MB) and keeps LOG_BACKUP_COUNT (5)
older copies (packrat-api.log.1 ... .5), so the directory is bounded at
about 60 MB per process. One file per process rather than one shared file
because Windows won't let two processes rotate the same file.

Everything also still goes to the console, so the PowerShell/terminal
windows behave exactly as before. LOG_LEVEL=DEBUG turns on the chatty
lower-level output (successful GET requests, SQL, netmiko/paramiko
handshakes) for the rare case where the defaults aren't enough.

Nothing here logs request bodies or secrets: passwords, SNMP communities
and enable secrets are never written, only who did what to which device.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import sys
from pathlib import Path

from app.config import get_settings

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(process_name)s:%(process)d] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Libraries whose INFO/DEBUG output is noise at the default level. They
# still surface at WARNING and above, and LOG_LEVEL=DEBUG lets them through.
NOISY_LOGGERS = (
    "paramiko",
    "paramiko.transport",
    "netmiko",
    "pysnmp",
    "asyncio",
    "aiosqlite",
    "sqlalchemy.engine",
    "celery.utils.functional",
    "kombu",
    "amqp",
    "watchfiles",
    "multipart",
    "httpx",
    "httpcore",
)

_configured_for: str | None = None


class _ProcessNameFilter(logging.Filter):
    def __init__(self, process_name: str) -> None:
        super().__init__()
        self.process_name = process_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.process_name = self.process_name
        return True


def log_dir() -> Path:
    return Path(get_settings().log_dir).expanduser().resolve()


def log_file_for(process_name: str) -> Path:
    return log_dir() / f"packrat-{process_name}.log"


def configure_logging(process_name: str) -> Path | None:
    """Idempotent. Returns the file this process logs to, or None if the
    directory couldn't be created (logging then stays console-only rather
    than taking the application down with it)."""
    global _configured_for
    if _configured_for == process_name:
        return log_file_for(process_name)

    settings = get_settings()
    level_name = (settings.log_level or "INFO").upper()
    level = logging.getLevelName(level_name)
    if not isinstance(level, int):
        level = logging.INFO

    root = logging.getLogger()
    # Drop handlers a previous call or a framework (uvicorn, celery) installed
    # so lines aren't duplicated; ours are re-added below.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(level)

    name_filter = _ProcessNameFilter(process_name)
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.addFilter(name_filter)
    root.addHandler(console)

    file_path: Path | None = None
    try:
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        file_path = directory / f"packrat-{process_name}.log"
        file_handler = logging.handlers.RotatingFileHandler(
            file_path,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(name_filter)
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning("Could not open log file under %s (%s); logging to console only", settings.log_dir, exc)

    # uvicorn and celery each install their own handlers by default; route
    # them through the root so they land in the file too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery", "celery.task", "celery.worker", "celery.beat"):
        framework_logger = logging.getLogger(name)
        for handler in list(framework_logger.handlers):
            framework_logger.removeHandler(handler)
        framework_logger.propagate = True
        framework_logger.setLevel(logging.NOTSET)
    # Our own middleware logs every request with more context than
    # uvicorn's access line, so that one is redundant.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    quiet_level = logging.DEBUG if level <= logging.DEBUG else logging.WARNING
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(quiet_level)

    # Python warnings that would otherwise print to stderr (unclosed
    # sockets, resource warnings) go to the log instead. Python's default
    # filters still hide third-party DeprecationWarnings, which are noise
    # for someone troubleshooting a device problem.
    logging.captureWarnings(True)

    _configured_for = process_name
    logging.getLogger("app").info(
        "Logging started for %s process (level %s, file %s) - Python %s on %s",
        process_name,
        level_name,
        file_path or "none",
        platform.python_version(),
        platform.platform(),
    )
    return file_path


def list_log_files() -> list[Path]:
    """Every current and rotated log file, newest first."""
    directory = log_dir()
    if not directory.is_dir():
        return []
    files = [p for p in directory.glob("packrat-*.log*") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def tail_file(path: Path, lines: int, max_bytes: int = 2_000_000) -> str:
    """The last `lines` lines of a (possibly large) file without reading
    all of it."""
    lines = max(1, lines)
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        chunk = min(size, max_bytes)
        fh.seek(size - chunk)
        data = fh.read(chunk)
    text = data.decode("utf-8", errors="replace")
    parts = text.splitlines()
    if chunk < size and parts:
        parts = parts[1:]  # the first line is almost certainly cut in half
    return "\n".join(parts[-lines:])


def system_info() -> str:
    """Environment summary bundled with a log download so the person
    reading it knows what they're looking at. Contains no secrets - the
    database URL is reduced to its dialect."""
    settings = get_settings()
    dialect = settings.database_url.split(":", 1)[0]
    rows = [
        ("Application", f"{settings.app_name} ({settings.environment})"),
        ("Python", f"{platform.python_version()} ({sys.executable})"),
        ("Platform", platform.platform()),
        ("Database", dialect),
        ("Celery eager mode", str(settings.celery_task_always_eager)),
        ("Log level", settings.log_level),
        ("Log directory", str(log_dir())),
        ("Working directory", os.getcwd()),
    ]
    return "\n".join(f"{k}: {v}" for k, v in rows) + "\n"
