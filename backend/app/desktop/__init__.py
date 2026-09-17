"""Packrat as a desktop application.

The same backend that runs under docker-compose or the PowerShell scripts,
packaged so a customer can install it like any other program: one
executable that starts the API, the worker and the scheduler as child
processes of itself, keeps them running, serves the built frontend, and
sits in the system tray (menu bar on a Mac) with Open / Quit.

Nothing outside the machine is needed - no Redis, no Postgres, no Node, no
Python install. Jobs travel from the API to the worker through a
SQLite-backed queue (kombu's SQLAlchemy transport) and everything the app
stores lives in one per-user folder:

    Windows   %LOCALAPPDATA%\\Packrat
    macOS     ~/Library/Application Support/Packrat
    Linux     ~/.local/share/packrat

    packrat.db          the database (devices, configs, history, users)
    queue.db            the job queue between API and worker
    packrat.env         generated secrets (JWT key, credential encryption
                        key) and any settings the user wants to override
    logs/               packrat-api.log, packrat-worker.log, packrat-beat.log,
                        packrat-desktop.log and the children's console output
    firmware_images/    uploaded firmware files
    celerybeat-schedule the scheduler's bookkeeping

Entry points: `python -m app.desktop` from backend/ during development;
the PyInstaller build (installer/packrat.spec) wraps the same code.
"""
