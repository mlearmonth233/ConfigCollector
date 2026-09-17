# Packrat desktop installers

Everything under `installer/` turns the backend plus the built frontend into
a program a customer installs like any other: a Windows Setup `.exe` and a
macOS `.dmg`. The running program is `Packrat` (or `Packrat.app`), which
starts the API, the worker and the scheduler as child processes, serves the
web UI itself on `http://127.0.0.1:8321`, opens the browser, and sits in the
system tray / menu bar with *Open*, *Open log folder*, *Start at login* and
*Quit*. No Redis, Python or Node on the customer's machine.

The behaviour lives in `backend/app/desktop/` and also runs unbundled:

```bash
cd backend && python -m app.desktop --no-tray      # console mode, Ctrl+C stops it
python -m app.desktop --data-dir /tmp/packrat-test --port 8399 --no-browser
```

(`frontend/dist` must exist for the UI to be served; `npm run build` in
`frontend/` makes it.)

## What the customer gets

| | Windows | macOS |
|---|---|---|
| Download | `Packrat-Setup-<version>-windows.exe` | `Packrat-<version>-macos-arm64.dmg` (Apple Silicon) or `-x86_64.dmg` (Intel) |
| Install | Run Setup; per-user, no admin prompt; Start Menu entry; optional "start when I sign in" | Drag Packrat to Applications |
| Runs as | Tray icon | Menu-bar icon (no Dock icon) |
| Data | `%LOCALAPPDATA%\Packrat` | `~/Library/Application Support/Packrat` |
| Uninstall | Settings > Apps, or the Start Menu entry; leaves the data folder | Drag to Bin; delete the data folder by hand if wanted |

The data folder holds `packrat.db` (everything), `queue.db` (the job
queue), `packrat.env` (generated secrets and optional overrides such as
`PACKRAT_PORT=8321`, `PACKRAT_HOST=0.0.0.0`, `LOG_LEVEL=DEBUG`), `logs/`
and `firmware_images/`. Back up `packrat.db` and `packrat.env` together.

## Building

**With GitHub Actions (recommended, needs no Mac):** push a tag.

```bash
git tag v0.1.0
git push origin v0.1.0
```

`.github/workflows/installers.yml` builds the Windows installer and both Mac
disk images on GitHub's runners, smoke-tests each bundle (starts it, checks
`/health` and that the UI is served), and publishes a GitHub Release with
the three files attached. *Actions > Installers > Run workflow* does the
same without a tag and leaves the files as run artifacts.

**On your own machine:**

```powershell
.\installer\build-windows.ps1          # -> dist\installer\Packrat-Setup-<version>-windows.exe
```

```bash
./installer/build-mac.sh               # -> dist/installer/Packrat-<version>-macos-<arch>.dmg
```

Both need Python 3.11-3.13 and Node 20+. Windows also needs
[Inno Setup 6](https://jrsoftware.org/isdl.php) for the Setup `.exe`
(without it the script stops after producing the portable folder
`dist\Packrat\`). A Mac can only build for its own CPU type.

The version comes from `installer/VERSION`, overridden by the tag on
Actions (`v0.2.0` builds `0.2.0`). Bump the file when you bump the tag so
local builds agree.

## Signing

**Windows.** The Setup `.exe` is unsigned, so SmartScreen shows "Windows
protected your PC" until the file has built up reputation; customers click
*More info > Run anyway*. An OV or EV code-signing certificate (Sectigo,
DigiCert, roughly £200-400 a year) removes that; sign `dist\Packrat\Packrat.exe`
and the Setup file with `signtool` after the build, or add a `SignTool`
directive to `packrat.iss`.

**macOS.** Without a certificate the build is ad-hoc signed: it runs (Apple
Silicon refuses unsigned binaries outright, so this matters), but Gatekeeper
says the developer cannot be verified and the customer must right-click >
*Open* once. An Apple Developer Program membership ($99 a year) provides a
*Developer ID Application* certificate; export it as a `.p12` and add these
repository secrets and the workflow signs and notarizes automatically:

| Secret | Value |
|---|---|
| `MACOS_CERTIFICATE_P12` | `base64 -i certificate.p12` |
| `MACOS_CERTIFICATE_PASSWORD` | the `.p12` export password |
| `MACOS_CODESIGN_IDENTITY` | `Developer ID Application: MIL Networks Limited (TEAMID)` |
| `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` | Apple ID, team id, and an app-specific password, for notarization |

Locally, set `CODESIGN_IDENTITY` (and `NOTARY_PROFILE` from
`xcrun notarytool store-credentials`) before running `build-mac.sh`.

## How the bundle differs from a server install

- **Queue.** `CELERY_BROKER_URL=sqla+sqlite:///.../queue.db`: kombu's
  SQLAlchemy transport, polled by the worker. No Redis. Remote control
  (`celery inspect`) is off because that transport has no fanout exchange;
  nothing in the app uses it.
- **One process serves everything.** `FRONTEND_DIST_DIR` makes the API
  serve the built UI with single-page-app fallback, so the UI talks to the
  API on the same origin and any free port works.
- **Secrets are generated** on first run into `packrat.env` and the
  environment is always `production` (placeholder secrets are refused).
- **Children watch the launcher.** Each child exits when the launcher's
  process disappears, so a crash never leaves a worker holding the
  database. The launcher restarts a child that dies, with back-off.
- **Second launch** while running just opens the browser.

## Files

- `packrat.spec` - PyInstaller build definition (data files, hidden imports).
- `packrat_desktop.py` - the frozen entry point (calls `app.desktop.app.main`).
- `packrat.iss` - Inno Setup script for the Windows installer.
- `build-windows.ps1`, `build-mac.sh` - full local builds.
- `requirements-desktop.txt` - PyInstaller, pystray, Pillow.
- `assets/` - icon as PNG (all platforms) and ICO (Windows).
- `VERSION` - the version local builds use.
