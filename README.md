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

## Adding devices

Devices can be added one at a time from the UI, or bulk-imported via CSV
(`Devices → Import CSV`). Expected columns:

```csv
name,host,port,device_type,site,credential_name,custom_commands
core-sw1,10.0.0.1,22,cisco_ios,DC1,labcred,
wlc-1,10.0.0.2,22,cisco_wlc,DC1,labcred,
pdu-1,10.0.0.4,22,pdu_generic,DC1,labcred,"about,show status"
```

- `credential_name` looks up an existing credential set by name within your
  org; leave blank to add the device without one.
- `custom_commands` (comma-separated) overrides the default "show config"
  command(s) for that device type — required for device types with no
  built-in default, like `pdu_generic` and `console_server`, since PDU/console
  server CLIs vary too much per vendor to guess.

See `GET /api/device-types` (or the "Device type" dropdown in the UI) for
the full supported list, spanning switches, WLCs, firewalls, PDUs, and
console servers. New device types are added in
`backend/app/services/device_types.py` by mapping to a Netmiko driver name.

## Security notes

- Device passwords/enable secrets are encrypted at rest with Fernet
  (`CREDENTIAL_ENCRYPTION_KEY`) and only decrypted in-memory inside the
  Celery worker process at the moment of connecting to a device.
- Auth is a JWT bearer token (no cookies), so CORS is open but
  credential-less.
- All data is scoped by `org_id`; cross-org access returns 404, not 403, to
  avoid leaking existence of other orgs' resources.

## Known limitations / next steps

- Schema is bootstrapped via `Base.metadata.create_all` at startup rather
  than Alembic migrations — fine for getting started, but add Alembic
  before running this against a production database with real data in it.
- No password reset / email verification flow.
- RBAC is a single `admin`/`member` flag per user; no per-device or
  per-team permissions yet.
- No scheduled/recurring collection jobs (only on-demand).
- No diffing between config snapshots yet — each collection just adds a new
  timestamped snapshot per device.
