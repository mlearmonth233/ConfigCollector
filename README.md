# ConfigCollector

A multi-tenant SaaS tool for bulk-collecting device configurations. Drop in a
list of network devices — Cisco switches, WLCs, firewalls, PDUs, console
servers, and more — assign credentials, and pull their running
configuration over SSH in one click (or via CSV bulk import for hundreds of
devices at once).

## Architecture

- **Backend**: FastAPI (async), SQLAlchemy 2.0, JWT auth. Device credentials
  are encrypted at rest (Fernet) and only decrypted in-memory when
  connecting to a device.
- **Collection**: [Netmiko](https://github.com/ktbyers/netmiko) drives the
  actual SSH sessions. Collection jobs fan out to a Celery worker (backed by
  Redis) so hundreds of devices can be collected in parallel without
  blocking the API.
- **Frontend**: React + TypeScript (Vite), talking to the backend over a
  JSON REST API.
- **Multi-tenancy**: every row is scoped to an `Organization`; users only
  see their own org's devices, credentials, and jobs.

```
frontend (React/Vite) --HTTP--> backend (FastAPI) --enqueues--> Celery worker --SSH/Netmiko--> devices
                                     |                                |
                                     +--------- Postgres -------------+
                                     |                                |
                                     +----------- Redis (broker) -----+
```

## Quickstart (Docker Compose)

```bash
cp .env.example .env   # then set JWT_SECRET_KEY and CREDENTIAL_ENCRYPTION_KEY
docker compose up --build
```

- Frontend: http://localhost:4173
- Backend API + docs: http://localhost:8000/docs

Generate a real encryption key before using this for anything beyond a demo:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Local development (without Docker)

**Backend** (uses a local SQLite file by default — no Postgres/Redis needed):

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
CELERY_TASK_ALWAYS_EAGER=true uvicorn app.main:app --reload
```

`CELERY_TASK_ALWAYS_EAGER=true` runs collection jobs synchronously in-process
instead of dispatching to a Celery worker over Redis — handy for local
testing without standing up the full stack. Omit it (and run `celery -A
app.celery_app worker --loglevel=info` alongside uvicorn, with Redis
running) to exercise the real async job queue.

Run the test suite:

```bash
python -m pytest
```

**Frontend**:

```bash
cd frontend
npm install
echo "VITE_API_BASE_URL=http://localhost:8000" > .env.local
npm run dev
```

### Windows / PowerShell

The commands above are bash syntax (`source`, inline `VAR=value cmd`) and
won't run as-is in PowerShell. Use the bundled scripts instead, which handle
venv creation, dependency install, and the Windows-specific syntax for you:

```powershell
.\backend\run-backend.ps1    # sets up + starts the backend
.\frontend\run-frontend.ps1  # sets up + starts the frontend, in another window
# or, from the repo root, start both at once (each in its own window):
.\run-dev.ps1
```

`run-frontend.ps1` automatically opens Chrome to http://localhost:5173 once
the dev server is actually ready (falling back to your system's default
browser if Chrome isn't installed) - no need to open it by hand.

If PowerShell blocks the scripts from running, allow local scripts once
(per user, doesn't need admin):
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### VS Code

Open the repo root as the workspace. `.vscode/launch.json` provides:
- **Backend (FastAPI)** — runs uvicorn under the Python debugger (breakpoints
  work). The first time, select the backend's interpreter via
  `Ctrl+Shift+P` → *Python: Select Interpreter* → `backend/.venv/...`.
- **Frontend (Vite)** — runs the dev server in a terminal.
- **Run ConfigCollector (Backend + Frontend)** — a compound that starts both
  with a single **F5**.

`.vscode/tasks.json` offers the same via *Terminal → Run Task* (including
one-time `Backend: Create venv` / `Install deps` setup tasks) if you'd
rather not use the debugger.

## Adding devices

Devices can be added one at a time from the UI, or bulk-imported via CSV
(`Devices → Import CSV`). **Download CSV template** next to it gets you a
ready-to-edit starting file with the exact columns expected and a few
example rows (`GET /api/devices/import-template` for scripted access):

```csv
name,host,port,device_type,site,credential_name,custom_commands,device_role,network_zone
core-sw1,10.0.0.1,22,cisco_ios,DC1,labcred,,,
wlc-1,10.0.0.2,22,cisco_wlc,DC1,labcred,,,
pdu-1,10.0.0.4,22,pdu_generic,DC1,labcred,"about,show status",,
GBGYSP01SWA001,10.0.0.5,22,,,labcred,,,
```

**Duplicate**, on any device's row, opens the "Add device" form pre-filled
with that device's fields - handy for a batch that's identical except for
a sequence number (a trailing number in the name, e.g. `SWA001`, is bumped
to `SWA002` automatically; every other field, including host, copies over
verbatim for you to adjust before saving).

- `credential_name` looks up an existing credential set by name within your
  org; leave blank to add the device without one.
- `custom_commands` (comma-separated) overrides the default "show config"
  command(s) for that device type — required for device types with no
  built-in default, like `pdu_generic` and `console_server`, since PDU/console
  server CLIs vary too much per vendor to guess.
- `device_type`, `device_role`, and `network_zone` are all optional - see
  "Auto-detecting device type/role/zone from the name" below. An explicit
  value in any of these columns always wins over a detected one; the last
  example row above (`GBGYSP01SWA001`) leaves all three blank and relies
  entirely on detection.

See `GET /api/device-types` (or the "Device type" dropdown in the UI) for
the full supported list, spanning switches, WLCs, firewalls, PDUs, and
console servers. New device types are added in
`backend/app/services/device_types.py` by mapping to a Netmiko driver name.

### Auto-detecting device type/role/zone from the name

Both the "Add device" form and CSV import can infer a device's type, role,
and network zone from its **name**, based on a short-code naming
convention (e.g. `GBGYSP01SWA001`):

| Code in name | Role                 | Device type (when unambiguous) |
| ------------ | -------------------- | ------------------------------- |
| `SWA`        | Access switch         | `cisco_ios`                     |
| `SWS`        | Server switch          | `cisco_ios`                     |
| `SWC`        | Core switch            | `cisco_ios`                     |
| `SWD`        | Distribution switch    | `cisco_ios`                     |
| `WLC`        | Wireless LAN controller | *(none - see below)*           |
| `PDU`        | Power distribution unit | `pdu_generic`                  |
| `CON`        | Console server          | `console_server`                |

`P0`/`O0` in the name similarly suggest the **network zone**, IT or OT.

This is always just a starting guess, shown live as you type a name in the
"Add device" form (and applied per-row on CSV import) - every field it
fills in stays fully editable, and picking something yourself before
saving always wins. A WLC is deliberately **never** guessed all the way to
a device type: the naming convention can't tell an older AireOS controller
from a Catalyst 9800 apart, so that choice is always left to you (both the
form and CSV import will ask for it explicitly rather than assume). The
same goes for any name with no recognizable code in it at all - a device
type must be specified by hand.

`GET /api/devices/detect?name=...` exposes the same detection for scripted
use, and `GET /api/device-roles` lists the full set of roles.

## TACACS+/RADIUS and MFA-backed logins

TACACS+/RADIUS AAA (and any MFA layered on top, e.g. Duo) is configured on
the device itself, not in this app — from ConfigCollector's point of view
that just changes how long a login takes and whether a one-time code is
needed. Each **credential** (`Devices → Credentials`) has:

- **MFA / AAA mode**:
  - `none` — plain username/password.
  - `push` — the device's AAA backend prompts out-of-band (e.g. a Duo push
    notification) and holds the login until it's approved. No extra input is
    needed here, just a generous `auth_timeout_seconds`.
  - `passcode` — a fresh one-time code is required on every collection run.
    It's never stored: you're prompted for it (per credential in use) when
    starting a collection, and it's appended to the password using the
    credential's delimiter (default `,`, e.g. `mypassword,123456`) — the
    convention most TACACS+/Duo integrations expect.
- **Auth timeout (seconds)** — how long to wait for the full login (SSH +
  the TACACS+/RADIUS round trip + any MFA challenge/approval) before giving
  up. Plain local-auth devices are fine with the default (45s); push-MFA
  credentials often need 60–90s to give a human time to approve.
- **Fallback credential** — tried if this credential fails outright, either
  at login (TACACS+/RADIUS unreachable, account locked, etc.) or afterward
  while entering enable/privileged mode (wrong or missing enable secret) —
  typically a device's local/default account, or some other break-glass
  login. Point it at another credential you've already created; a
  credential can only be picked as a fallback if it has no fallback of its
  own (one level deep, so a device tries at most two credentials). If the
  primary succeeds all the way through, the fallback is never touched; if
  both are tried and both fail, the job item's error message says so for
  each. When the fallback is what actually got a device in, its job item is
  marked "fallback used" — worth a look, since it usually means the
  primary AAA path is degraded (or, for an enable-mode fallback, that the
  primary credential's enable secret needs attention).

### Queued, two-phase collection

Within a job, devices are collected one at a time, in the order they were
added, but the next device is dispatched as soon as the current one
finishes *authenticating* - not once it's fully done. That overlaps a
device's (usually fast) command-running phase with the next device's
(usually slow, TACACS+/RADIUS-bound) authentication phase, without ever
having two devices authenticating at the same time - so a large batch still
can't hammer TACACS+/RADIUS or the network with concurrent logins, but
doesn't sit idle waiting on a device's commands to finish either. Each
device goes through two phases in order:

1. **Authenticating** — connect and log in (this is the phase a slow
   TACACS+/RADIUS round trip or an MFA approval affects). No command is sent
   to the device until this succeeds.
2. **Running** — the configured command(s) are sent and the output is
   captured as a config snapshot.

The job detail page shows each device's current phase live, and a banner
reminds you to approve any pending push notification while devices are
authenticating. A status bar at the top summarizes the whole job at a
glance - a segmented progress bar plus a count for each phase present
(pending, authenticating, running, completed, failed) - so you can tell
which devices are still queued, logging in, running commands, done, or
failed without reading every row. A **Retry** button on any failed
device's row starts a fresh one-device job for just that device (same
Start collection dialog as anywhere else, so you can fix a command/OTP
before trying again) - no need to rerun the whole batch for one bad
device. Click **Console** on any device's row to open a live,
auto-scrolling transcript - connecting, authenticated, each command sent and
its output, and (if it fails) which credential failed and why - updating as
the job progresses via the same polling that drives the status column.

While a job is still pending or running, every device's console opens
automatically the moment the page loads, so you can watch every switch being
collected from at once without clicking into each one - a console you
collapse manually stays collapsed on later polls instead of reopening. An
**Expand all consoles** / **Collapse all consoles** toggle in the page
header does the same for a job at any stage, including one that's already
finished.

### Setting default commands per device type

The **Commands** page (`Commands` in the nav) lets you change what runs by
default for each device type, org-wide - no more editing every device or
re-typing commands on every run. Each device type shows a checklist of
common `show` commands for its category (switch, WLC, firewall, PDU,
console server) plus a free-text field for anything not on the list.
Several device types default to a full audit-style command set out of the
box - Cisco IOS switches/routers collect interfaces, neighbors, VLANs/port-
channels, routing, QoS, and hardware/environmentals in one pass; both WLC
generations and APC PDUs have their own equivalent built-in sets too (see
`backend/app/services/device_types.py` for the exact commands). **Save**
persists your choice for the whole org; **Reset to default** discards a
customization and goes back to the built-in default.

Priority when a device is actually collected, most specific wins:

1. A one-time override entered when starting a collection (see below) - for
   that run only.
2. The device's own `custom_commands` (set on the device itself).
3. The org's saved Commands-page default for that device type.
4. The built-in registry default.

### Choosing commands per run

When you start a collection (`Collect all`/`Collect selected`), a dialog
lists every device type among the targeted devices (grouped by category —
switch, WLC, firewall, PDU, console server) with its default command(s)
pre-filled and editable, plus a one-time-passcode field for any credential
in use that requires one — including a passcode-mode fallback credential,
since which one ends up authenticating a device isn't known until it's
actually contacted, so its code has to be supplied up front just in case.
These command overrides apply to that run only — they don't change a
device's own saved `custom_commands`.

### Rerunning old jobs

On the **Jobs** page, **Rerun** (per job row) or **Rerun selected** (after
checking several) starts a fresh collection against the same devices as
before - it opens the same "Start collection" dialog you'd get from
Devices, letting you adjust commands/OTPs for this run same as any other.
Rerunning several jobs at once combines their devices into a single new
job (duplicates only counted once); any device since deleted is silently
skipped (with a note if that happens to be all of them).

### Downloading a collected config

From a job's device row, "View config" opens the collected snapshot, with a
**Download** button offering:

- **File type** — `.txt` (default) or `.log`.
- **Add timestamp to filename** — off by default, so the download is just
  `<hostname>.txt`; check it to get `<hostname>_<collected-at>.txt` instead,
  useful when keeping multiple snapshots of the same device side by side.

Both choices are remembered in your browser (`localStorage`) for next time.
The filename always starts from the device's name in ConfigCollector, with
anything unsafe for a filename replaced — `GET /api/snapshots/{id}/download`
takes the same `ext`/`include_timestamp` query params directly, for scripted
access.

**Bulk download**: once at least one device in a job has a collected config,
the job detail page shows the same file type/timestamp controls next to a
**Download all** button, which bundles every completed device's config from
that job into a single ZIP (one file per device, named the same way as a
single download; two devices sharing a name get a numeric suffix so neither
is silently overwritten). `GET /api/jobs/{id}/download` takes the same
`ext`/`include_timestamp` query params for scripted access.

## Security notes

- Device passwords/enable secrets are encrypted at rest with Fernet
  (`CREDENTIAL_ENCRYPTION_KEY`) and only decrypted in-memory inside the
  Celery worker process at the moment of connecting to a device.
- Auth is a JWT bearer token (no cookies), so CORS is open but
  credential-less.
- All data is scoped by `org_id`; cross-org access returns 404, not 403, to
  avoid leaking existence of other orgs' resources.
- One-time passcodes are never persisted: they're passed through to the
  Celery task that uses them and discarded after that run. They do transit
  the Redis broker in flight (as task arguments), which is expected — treat
  Redis like any other piece of internal infrastructure that shouldn't be
  exposed publicly.
- Legacy SSH support: some still-deployed switches/WLCs only offer
  `diffie-hellman-group14-sha1` for key exchange and/or `ssh-rsa` (RSA host
  key, SHA-1 signature) for the host key, both of which recent Paramiko
  releases dropped entirely. `collector.py` reimplements just these two
  (the kex algorithm shares its well-vetted 2048-bit group with its still-
  supported SHA-256 sibling; the host key is an ordinary RSA key, just
  verified with the older signature scheme) and appends each to the *end*
  of Paramiko's preferred list, so either is only ever used when a device
  offers nothing stronger — never at the expense of a better algorithm a
  device does support. The weaker, dynamically-negotiated
  `diffie-hellman-group-exchange-sha1` kex is deliberately not re-enabled.

## Known limitations / next steps

- Schema is bootstrapped via `Base.metadata.create_all` at startup rather
  than Alembic migrations — fine for getting started, but add Alembic
  before running this against a production database with real data in it.
  For the local SQLite dev database specifically, a model gaining a new
  column is detected automatically at startup and the file is reset (with a
  console message explaining why) rather than crashing on the first request
  that touches it — since that data is disposable anyway. This detection
  never runs against Postgres.
- MFA support covers the two most common device-side patterns: a push
  approval you wait out with a longer timeout, and a passcode appended to
  the password. A device whose AAA presents its own extra interactive CLI
  prompt (beyond the initial SSH password) isn't handled and would need a
  per-vendor Netmiko subclass.
- No password reset / email verification flow.
- RBAC is a single `admin`/`member` flag per user; no per-device or
  per-team permissions yet.
- No scheduled/recurring collection jobs (only on-demand).
- No diffing between config snapshots yet — each collection just adds a new
  timestamped snapshot per device.
