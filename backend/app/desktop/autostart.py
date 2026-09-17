"""Start Packrat when the user signs in: a Run key on Windows, a LaunchAgent
on macOS, an autostart .desktop entry elsewhere. Always with --no-browser,
so signing in does not pop a browser tab."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

from app.desktop.paths import APP_NAME, BUNDLE_ID, launcher_command

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _command() -> list[str]:
    return [*launcher_command(), "--no-browser"]


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"


def _desktop_entry() -> Path:
    return Path.home() / ".config" / "autostart" / "packrat.desktop"


def is_enabled() -> bool:
    try:
        if sys.platform == "win32":
            import winreg  # noqa: PLC0415

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, APP_NAME)
                return True
        if sys.platform == "darwin":
            return _plist_path().is_file()
        return _desktop_entry().is_file()
    except OSError:
        return False


def enable() -> None:
    if sys.platform == "win32":
        import winreg  # noqa: PLC0415

        value = subprocess.list2cmdline(_command())
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, value)
        return
    if sys.platform == "darwin":
        path = _plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": BUNDLE_ID,
            "ProgramArguments": _command(),
            "RunAtLoad": True,
            "ProcessType": "Background",
        }
        path.write_bytes(plistlib.dumps(payload))
        return
    path = _desktop_entry()
    path.parent.mkdir(parents=True, exist_ok=True)
    exec_line = " ".join(f'"{part}"' if " " in part else part for part in _command())
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=Packrat\nComment=Network config backup\n"
        f"Exec={exec_line}\nX-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )


def disable() -> None:
    try:
        if sys.platform == "win32":
            import winreg  # noqa: PLC0415

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_NAME)
            return
        path = _plist_path() if sys.platform == "darwin" else _desktop_entry()
        path.unlink(missing_ok=True)
    except OSError:
        pass


def toggle() -> bool:
    if is_enabled():
        disable()
        return False
    enable()
    return True
