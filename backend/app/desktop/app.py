"""The launcher: `Packrat` (or `python -m app.desktop`).

    Packrat                  start everything, open the browser, sit in the tray
    Packrat --no-browser     the same without opening a browser (start at login)
    Packrat --no-tray        console mode: Ctrl+C stops everything
    Packrat --role api       (internal) one of the three child processes
    Packrat --data-dir DIR   keep the data somewhere else (also PACKRAT_DATA_DIR)

A second launch while Packrat is already running just opens the browser.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import socket
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from app.desktop.paths import APP_NAME, data_dir

log = logging.getLogger("app.desktop")


def _ensure_std_streams() -> None:
    """A windowed (no console) build has no stdout/stderr at all; anything
    that prints would raise. Point them at nowhere."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


def _configure_launcher_logging(log_dir: Path) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)-8s [desktop:%(process)d] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(log_dir / "packrat-desktop.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except OSError as exc:
        root.warning("Could not open the launcher log file: %s", exc)


def _health(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=timeout) as response:  # noqa: S310 - our own localhost URL
            return response.status == 200 and b'"ok"' in response.read()
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host if host != "0.0.0.0" else "127.0.0.1", port)) == 0  # noqa: S104


def _free_port(host: str, start: int) -> int:
    for port in range(start, start + 50):
        if not _port_in_use(host, port):
            return port
    raise RuntimeError(f"No free port between {start} and {start + 49}")


def _wait_for_api(url: str, supervisor, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _health(url):
            return True
        api = supervisor.children.get("api")
        if api is not None and api.process is not None and api.process.poll() is not None:
            # It died while starting; let the monitor's restart have a go
            # but do not wait forever on a broken install.
            time.sleep(1)
            continue
        time.sleep(0.5)
    return False


def _console_loop(url: str, supervisor) -> None:
    stop = {"requested": False}

    def request_stop(*_args) -> None:
        stop["requested"] = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, request_stop)
        except (ValueError, OSError):
            pass
    print(f"{APP_NAME} is running at {url}. Press Ctrl+C to stop.", flush=True)
    try:
        while not stop["requested"]:
            time.sleep(0.5)
    finally:
        supervisor.stop()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME.lower(), description=f"{APP_NAME} desktop launcher")
    parser.add_argument("--role", choices=("api", "worker", "beat"), help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser when ready")
    parser.add_argument("--no-tray", action="store_true", help="console mode instead of a tray icon")
    parser.add_argument("--data-dir", help="folder for the database, logs and settings")
    parser.add_argument("--port", type=int, help="listen on this port instead of the saved one")
    parser.add_argument("--wait", type=float, default=90.0, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _ensure_std_streams()
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.data_dir:
        os.environ["PACKRAT_DATA_DIR"] = args.data_dir

    if args.role:
        from app.desktop.roles import run_role  # noqa: PLC0415

        return run_role(args.role)

    data = data_dir()
    from app.desktop.environment import prepare_broker, prepare_environment  # noqa: PLC0415

    env = prepare_environment(data)
    os.chdir(data)
    _configure_launcher_logging(data / "logs")
    import platform  # noqa: PLC0415

    log.info(
        "%s starting; data in %s; %s %s on %s, Python %s%s",
        APP_NAME,
        data,
        platform.system(),
        platform.release(),
        platform.machine(),
        platform.python_version(),
        " (bundled)" if getattr(sys, "frozen", False) else "",
    )

    host = env.get("PACKRAT_HOST", "127.0.0.1")
    port = args.port or int(env.get("PACKRAT_PORT", "8321"))
    browse_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host  # noqa: S104
    url = f"http://{browse_host}:{port}"

    if _health(url):
        log.info("%s is already running at %s; opening it", APP_NAME, url)
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    if _port_in_use(host, port):
        new_port = _free_port(host, port + 1)
        log.warning("Port %s is taken by another program; using %s for this run", port, new_port)
        port = new_port
        url = f"http://{browse_host}:{port}"
    env["PACKRAT_PORT"] = os.environ["PACKRAT_PORT"] = str(port)

    try:
        prepare_broker(env["CELERY_BROKER_URL"])
    except Exception:  # noqa: BLE001
        log.exception("Could not prepare the job queue; the worker will try again itself")

    from app.desktop.supervisor import Supervisor  # noqa: PLC0415

    supervisor = Supervisor(env, data)
    supervisor.start("api")
    if not _wait_for_api(url, supervisor, args.wait):
        log.error("The API did not come up within %.0f seconds; see %s", args.wait, data / "logs" / "packrat-api.console.log")
        supervisor.stop()
        _report_startup_failure(data)
        return 1
    supervisor.start("worker")
    supervisor.start("beat")
    supervisor.start_monitor()
    log.info("%s ready at %s", APP_NAME, url)

    if not args.no_browser:
        webbrowser.open(url)

    from app.desktop.tray import run_tray, tray_available  # noqa: PLC0415

    if args.no_tray or not tray_available():
        _console_loop(url, supervisor)
        return 0
    try:
        run_tray(url, data / "logs", supervisor.stop, notify=f"Running at {url}. Right-click the icon to open or quit.")
    except Exception:  # noqa: BLE001
        log.exception("Tray icon failed; falling back to console mode")
        _console_loop(url, supervisor)
    return 0


def _report_startup_failure(data: Path) -> None:
    """Best effort: a windowed build has nowhere to print, so show a dialog
    where the platform makes that cheap."""
    message = f"{APP_NAME} could not start. The logs are in:\n{data / 'logs'}"
    try:
        if sys.platform == "win32":
            import ctypes  # noqa: PLC0415

            ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess  # noqa: PLC0415

            script = f'display alert "{APP_NAME}" message "{message}" as critical'
            subprocess.run(["osascript", "-e", script], check=False, timeout=30)  # noqa: S603, S607
        else:
            print(message, file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    raise SystemExit(main())
