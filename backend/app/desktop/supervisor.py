"""Starts the api/worker/beat children, restarts them if they die, stops
them all on quit."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.desktop.paths import backend_dir, is_frozen, launcher_command

log = logging.getLogger("app.desktop")

CREATE_NO_WINDOW = 0x08000000  # Windows: no console window for the children


@dataclass
class Child:
    role: str
    process: subprocess.Popen | None = None
    restarts: int = 0
    started_at: float = 0.0
    console_log: object = field(default=None, repr=False)

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None


class Supervisor:
    def __init__(self, env: dict[str, str], data_dir: Path) -> None:
        self.env = dict(env)
        self.env["PACKRAT_PARENT_PID"] = str(os.getpid())
        if not is_frozen():
            # The children run from the data folder; unbundled they still
            # need to import `app` from backend/.
            existing = self.env.get("PYTHONPATH", "")
            self.env["PYTHONPATH"] = str(backend_dir()) + (os.pathsep + existing if existing else "")
        self.data_dir = data_dir
        self.children: dict[str, Child] = {}
        self._stopping = False
        self._lock = threading.Lock()
        self._monitor: threading.Thread | None = None

    # -- starting -------------------------------------------------------------

    def start(self, role: str) -> Child:
        with self._lock:
            child = self.children.setdefault(role, Child(role))
            self._spawn(child)
            return child

    def _spawn(self, child: Child) -> None:
        # Each child's console output (anything printed before its file
        # logging is up, and any crash traceback) goes to its own file,
        # truncated on every start so it only ever shows the current run.
        log_path = self.data_dir / "logs" / f"packrat-{child.role}.console.log"
        if child.console_log is not None:
            try:
                child.console_log.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        child.console_log = open(log_path, "wb")  # noqa: SIM115 - closed on the next spawn / on stop
        kwargs: dict = {
            "cwd": str(self.data_dir),
            "env": self.env,
            "stdin": subprocess.DEVNULL,
            "stdout": child.console_log,
            "stderr": subprocess.STDOUT,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        command = [*launcher_command(), "--role", child.role]
        child.process = subprocess.Popen(command, **kwargs)  # noqa: S603
        child.started_at = time.monotonic()
        log.info("Started %s (pid %s)", child.role, child.process.pid)

    # -- watching -------------------------------------------------------------

    def start_monitor(self) -> None:
        self._monitor = threading.Thread(target=self._watch, name="supervisor", daemon=True)
        self._monitor.start()

    def _watch(self) -> None:
        while not self._stopping:
            time.sleep(1)
            with self._lock:
                for child in self.children.values():
                    if self._stopping or child.running or child.process is None:
                        continue
                    code = child.process.returncode
                    uptime = time.monotonic() - child.started_at
                    # A child that ran for a while gets a clean slate; one that
                    # keeps dying at start backs off up to a minute.
                    child.restarts = 0 if uptime > 300 else child.restarts + 1
                    delay = min(60, 2 ** min(child.restarts, 6))
                    log.error("%s exited with code %s after %.0fs; restarting in %ss", child.role, code, uptime, delay)
                    child.process = None
                    threading.Timer(delay, self._restart, args=(child,)).start()

    def _restart(self, child: Child) -> None:
        with self._lock:
            if self._stopping or child.running:
                return
            try:
                self._spawn(child)
            except Exception:  # noqa: BLE001
                log.exception("Could not restart %s", child.role)

    # -- stopping -------------------------------------------------------------

    def stop(self, timeout: float = 15.0) -> None:
        self._stopping = True
        with self._lock:
            procs = [c.process for c in self.children.values() if c.process is not None and c.process.poll() is None]
            for proc in procs:
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
            deadline = time.monotonic() + timeout
            for proc in procs:
                try:
                    proc.wait(timeout=max(0.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    log.warning("pid %s did not stop in time; killing it", proc.pid)
                    proc.kill()
            for child in self.children.values():
                if child.console_log is not None:
                    try:
                        child.console_log.close()  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        pass
        log.info("All processes stopped")

    def status(self) -> dict[str, bool]:
        return {role: child.running for role, child in self.children.items()}
