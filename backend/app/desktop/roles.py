"""The three child processes: `Packrat --role api|worker|beat`.

Each is the same executable as the launcher with a role argument, run with
the environment prepare_environment() exported. A watchdog thread ends the
child the moment the launcher that started it is gone, so a crashed or
killed launcher never leaves orphans holding the database or the port.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

ROLES = ("api", "worker", "beat")
log = logging.getLogger("app.desktop")


def _parent_alive(pid: int) -> bool:
    try:
        import psutil  # noqa: PLC0415

        return psutil.pid_exists(pid)
    except Exception:  # noqa: BLE001 - psutil is a hard dependency, but never let the check itself kill us
        if os.name == "nt":
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def _watch_parent() -> None:
    pid_text = os.environ.get("PACKRAT_PARENT_PID")
    if not pid_text:
        return
    pid = int(pid_text)

    def watch() -> None:
        while True:
            time.sleep(2)
            if not _parent_alive(pid):
                log.warning("Launcher (pid %s) is gone; stopping this %s process", pid, os.environ.get("PACKRAT_ROLE", "child"))
                os._exit(0)

    threading.Thread(target=watch, name="parent-watchdog", daemon=True).start()


def run_role(role: str) -> int:
    if role not in ROLES:
        print(f"Unknown role {role!r}; expected one of {', '.join(ROLES)}", file=sys.stderr)
        return 2
    os.environ["PACKRAT_ROLE"] = role
    _watch_parent()

    if role == "api":
        import uvicorn  # noqa: PLC0415

        from app.main import app  # noqa: PLC0415

        host = os.environ.get("PACKRAT_HOST", "127.0.0.1")
        port = int(os.environ.get("PACKRAT_PORT", "8321"))
        # log_config=None keeps the app's own file logging (configured when
        # app.main was imported) instead of uvicorn's defaults.
        uvicorn.run(app, host=host, port=port, log_config=None, access_log=False)
        return 0

    from app.celery_app import celery_app  # noqa: PLC0415

    if role == "worker":
        # Threads rather than prefork: the collector is I/O bound and the
        # prefork pool does not exist on Windows. Gossip, mingle and
        # heartbeats are for clusters of workers talking over a real broker.
        celery_app.worker_main(
            argv=[
                "worker",
                "--loglevel=info",
                "--pool=threads",
                "--concurrency=8",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
            ]
        )
        return 0

    celery_app.start(argv=["beat", "--loglevel=info"])
    return 0
