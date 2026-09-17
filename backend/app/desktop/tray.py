"""System-tray (menu-bar) icon: Open Packrat, Open log folder, Start at
login, Quit. pystray needs a display and a platform backend; when either
is missing tray_available() says so and the launcher runs as a plain
console program instead."""

from __future__ import annotations

import logging
import sys
import webbrowser
from collections.abc import Callable
from pathlib import Path

from app.desktop import autostart
from app.desktop.paths import APP_NAME, icon_file, open_in_file_manager

log = logging.getLogger("app.desktop")


def tray_available() -> bool:
    if sys.platform.startswith("linux"):
        import os  # noqa: PLC0415

        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return False
    try:
        import PIL.Image  # noqa: F401, PLC0415
        import pystray  # noqa: F401, PLC0415
    except Exception as exc:  # noqa: BLE001 - pystray raises platform-specific errors at import
        log.info("Tray icon not available (%s); running in console mode", exc)
        return False
    return True


def _load_image():
    from PIL import Image, ImageDraw  # noqa: PLC0415

    path = icon_file()
    if path is not None:
        try:
            return Image.open(path).convert("RGBA")
        except Exception:  # noqa: BLE001
            log.warning("Could not read %s; using a plain icon", path)
    image = Image.new("RGBA", (64, 64), (15, 23, 42, 255))
    ImageDraw.Draw(image).ellipse((12, 12, 52, 52), fill=(34, 211, 238, 255))
    return image


def run_tray(url: str, log_dir: Path, on_quit: Callable[[], None], notify: str | None = None) -> None:
    """Blocks until the user chooses Quit. Must run on the main thread
    (macOS requires it)."""
    import pystray  # noqa: PLC0415

    def open_app(icon=None, item=None) -> None:
        webbrowser.open(url)

    def open_logs(icon=None, item=None) -> None:
        open_in_file_manager(log_dir)

    def toggle_autostart(icon=None, item=None) -> None:
        try:
            autostart.toggle()
        except Exception:  # noqa: BLE001
            log.exception("Could not change the start-at-login setting")

    def quit_app(icon, item=None) -> None:
        icon.visible = False
        try:
            on_quit()
        finally:
            icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(f"Open {APP_NAME}", open_app, default=True),
        pystray.MenuItem("Open log folder", open_logs),
        pystray.MenuItem(
            f"Start {APP_NAME} when I sign in",
            toggle_autostart,
            checked=lambda item: autostart.is_enabled(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(f"Quit {APP_NAME}", quit_app),
    )
    icon = pystray.Icon(APP_NAME, _load_image(), f"{APP_NAME} - running at {url}", menu)

    def on_ready(icon) -> None:
        icon.visible = True
        if notify and getattr(icon, "HAS_NOTIFICATION", False):
            try:
                icon.notify(notify, APP_NAME)
            except Exception:  # noqa: BLE001
                pass

    icon.run(setup=on_ready)
