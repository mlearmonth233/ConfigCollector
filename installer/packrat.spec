# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build of the Packrat desktop app.

    pyinstaller --noconfirm installer/packrat.spec

Produces dist/Packrat/ (Windows, Linux) or dist/Packrat.app (macOS): one
folder holding the launcher executable, the Python runtime, every
dependency, the Alembic migrations and the built frontend. The
installer scripts wrap that folder. Environment knobs:

    PACKRAT_VERSION        version string (default: installer/VERSION)
    PACKRAT_BUILD_CONSOLE  1 = console executable (debugging the bundle)
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH is injected by PyInstaller
BACKEND = ROOT / "backend"
INSTALLER = ROOT / "installer"
FRONTEND_DIST = ROOT / "frontend" / "dist"
VERSION = os.environ.get("PACKRAT_VERSION") or (INSTALLER / "VERSION").read_text().strip()
CONSOLE = os.environ.get("PACKRAT_BUILD_CONSOLE", "") == "1"

if not (FRONTEND_DIST / "index.html").is_file():
    raise SystemExit("frontend/dist/index.html is missing - run `npm run build` in frontend/ first")

# Files the app opens by path at run time (not Python imports).
datas = [
    (str(BACKEND / "alembic"), "alembic"),
    (str(BACKEND / "alembic.ini"), "."),
    (str(FRONTEND_DIST), "frontend"),
    (str(INSTALLER / "assets" / "packrat-icon.png"), "."),
]
binaries = []
hiddenimports = [
    "app.desktop",
    "app.desktop.app",
    "app.desktop.roles",
    # pystray picks its backend at import time; importing it on a machine
    # without a display fails, so collect_all below may see nothing. Name
    # the backends outright (the ones for other platforms just warn).
    "pystray._base",
    "pystray._util",
    "pystray._win32",
    "pystray._util.win32",
    "pystray._darwin",
    "pystray._xorg",
    "pystray._appindicator",
    "pystray._gtk",
    # kombu's SQL transport and the SQLite result-free set-up.
    "kombu.transport.sqlalchemy",
    "kombu.transport.sqlalchemy.models",
]

# Packages that import their own modules by name at run time (Celery's
# pools, transports and backends; uvicorn's loop and protocol classes;
# pystray's platform backend), or load data files from their own folder
# (pysnmp's MIB modules, tzdata's zone database).
for package in ("celery", "kombu", "pystray", "pysnmp", "pysmi", "pyasn1", "tzdata"):
    try:
        d, b, h = collect_all(package)
    except Exception:  # noqa: BLE001 - optional (pysmi may be absent)
        continue
    datas += d
    binaries += b
    hiddenimports += h

for package in (
    "uvicorn",
    "netmiko",
    "passlib.handlers",
    "jose",
    "sqlalchemy.dialects.sqlite",
    "aiosqlite",
    "asyncssh",
    "pyftpdlib",
    "openpyxl",
    "email_validator",
    "app",
):
    hiddenimports += collect_submodules(package)

datas += collect_data_files("email_validator")
datas += collect_data_files("certifi")

block_cipher = None

a = Analysis(  # noqa: F821
    [str(INSTALLER / "packrat_desktop.py")],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest", "IPython", "matplotlib", "numpy", "pandas", "psycopg2", "asyncpg"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

icon = str(INSTALLER / "assets" / ("packrat.ico" if sys.platform == "win32" else "packrat-icon.png"))

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Packrat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    disable_windowed_traceback=False,
    icon=icon,
    version=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Packrat",
)

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="Packrat.app",
        icon=icon,
        bundle_identifier="uk.co.milnetworks.packrat",
        version=VERSION,
        info_plist={
            "CFBundleName": "Packrat",
            "CFBundleDisplayName": "Packrat",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHumanReadableCopyright": "MIL Networks Limited",
            "NSHighResolutionCapable": True,
            # Menu-bar app: no Dock icon, the tray icon is the way in.
            "LSUIElement": True,
            "LSMinimumSystemVersion": "12.0",
        },
    )
