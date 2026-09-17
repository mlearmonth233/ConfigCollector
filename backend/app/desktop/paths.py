"""Where the desktop build finds its own files and keeps the user's data."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Packrat"
BUNDLE_ID = "uk.co.milnetworks.packrat"
DEFAULT_PORT = 8321


def is_frozen() -> bool:
    """True inside a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def backend_dir() -> Path:
    """The backend/ source folder (development) or the bundle's internal
    folder (frozen), which is laid out the same way: `alembic/` and
    `alembic.ini` sit next to the `app` package either way."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def frontend_dist() -> Path | None:
    """The built frontend to serve, or None if there is not one."""
    override = os.environ.get("PACKRAT_FRONTEND_DIST")
    if override:
        return Path(override)
    if is_frozen():
        return backend_dir() / "frontend"
    candidate = backend_dir().parent / "frontend" / "dist"
    return candidate if (candidate / "index.html").is_file() else None


def icon_file() -> Path | None:
    for candidate in (
        backend_dir() / "packrat-icon.png",
        backend_dir().parent / "installer" / "assets" / "packrat-icon.png",
        backend_dir().parent / "frontend" / "public" / "pwa-512x512.png",
    ):
        if candidate.is_file():
            return candidate
    return None


def data_dir() -> Path:
    """Per-user data folder. PACKRAT_DATA_DIR overrides it (a portable
    install, or tests)."""
    override = os.environ.get("PACKRAT_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME.lower()


def launcher_command() -> list[str]:
    """How to start another copy of this program (used for the child
    processes and the start-at-login entry)."""
    if is_frozen():
        return [sys.executable]
    return [sys.executable, "-m", "app.desktop"]


def open_in_file_manager(path: Path) -> None:
    import subprocess  # noqa: PLC0415

    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603, S607
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607
