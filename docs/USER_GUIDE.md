# Packrat User Guide

Packrat hoards every network config. It logs into your switches,
controllers, firewalls and PDUs over SSH, stores what it finds, shows you
what changed, and does it again on a schedule. This guide walks through
every page of the app in the order you will meet them.

**Contents**

1. [Install and start](#1-install-and-start)
2. [First login and your organization](#2-first-login-and-your-organization)
3. [Credentials](#3-credentials)
4. [Devices](#4-devices)
5. [Commands](#5-commands)
6. [Jobs: collecting configs](#6-jobs-collecting-configs)
7. [History and diffs](#7-history-and-diffs)
8. [Schedules](#8-schedules)
9. [Firmware push](#9-firmware-push)
10. [DNS Check](#10-dns-check)
11. [Terminal](#11-terminal)
12. [Settings](#12-settings)
13. [Troubleshooting](#13-troubleshooting)
14. [Security notes](#14-security-notes)

---

## 1. Install and start

Packrat is three processes: a backend API, a worker that talks to devices,
and the web frontend. A fourth, the scheduler ("beat"), is needed only if
you use Schedules or snapshot retention.

### Windows (PowerShell)

1. Install Python 3.11 or newer, Node.js 20 or newer, and
   [Memurai](https://www.memurai.com/) (a Redis-compatible Windows
   service; the worker uses it as a queue).
2. Clone the repository and open PowerShell in its root folder.
3. If PowerShell refuses to run scripts, allow local scripts once:
   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   ```
4. Start everything:
   ```powershell
   .\run-dev.ps1
   ```
   This opens separate windows for the backend, the worker, the scheduler
   and the frontend. The first run installs dependencies and takes a few
   minutes; later runs skip that when nothing changed.
5. The frontend opens in your browser at http://localhost:5173.

To run without Memurai (jobs then run inside the backend and "Start
collection" waits until they finish), use `.\run-dev.ps1 -Eager`.

### Linux and macOS

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload                 # API on :8000
celery -A app.celery_app worker --loglevel=info   # in a second terminal
celery -A app.celery_app beat --loglevel=info     # in a third, for Schedules

cd ../frontend
npm install
echo "VITE_API_BASE_URL=http://localhost:8000" > .env.local
npm run dev                                   # UI on :5173
```

Redis must be reachable at `redis://localhost:6379/0` for the worker.
Without Redis, start the API with `CELERY_TASK_ALWAYS_EAGER=true` instead
of running a worker.

### Docker Compose

```bash
cp .env.example .env    # set JWT_SECRET_KEY and CREDENTIAL_ENCRYPTION_KEY
docker compose up --build
```

Frontend on http://localhost:4173, API docs on http://localhost:8000/docs.
This stack uses Postgres and Redis and is the recommended shape for a
shared server.

### Before anything real

Generate a real encryption key and put it in `.env` as
`CREDENTIAL_ENCRYPTION_KEY`. Device passwords are encrypted with it:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

If you change this key later, existing credentials can no longer be
decrypted and must be re-entered.

---

## 2. First login and your organization

Open the app and choose **Create one** under the login form. Enter an
organization name, your email and a password. The first user in an
organization is its admin.

Everything you create (devices, credentials, jobs, schedules) belongs to
your organization and is invisible to other organizations on the same
server.

To add colleagues, an admin uses the users API (`POST /api/users` in the
API docs at `/docs`) with their email and a starting password. Non-admin
users can run collections and view results but cannot manage schedules.

---

## 3. Credentials

**Credentials** page. Set these up before adding devices; a device with no
credential cannot be collected.

**Add a credential** with a name (for example "TACACS - netops"), the
username and password, and optionally:

- **Enable secret**: for platforms with a privileged mode (Cisco IOS,
  NX-OS, 9800). Leave empty if the account lands in enable mode already.
- **MFA / AAA mode**:
  - *None*: plain username and password.
  - *Push*: the AAA server sends a push notification (Duo, Okta Verify).
    Packrat waits at the login step until you approve. Raise **Auth
    timeout** to 60 or 90 seconds so it does not give up while you reach
    for your phone.
  - *Passcode*: a one-time code must be appended to the password. When you
    start a job you are asked for the code, and Packrat joins it to the
    password using the **OTP delimiter** your AAA server expects (usually a
    comma).
- **Auth timeout**: how long to wait for the SSH login to succeed. TACACS+
  logins are often slow; 45 seconds is the default.
- **Fallback**: another credential to try automatically if this one is
  rejected or cannot enter enable mode. Typical use: a TACACS+ account with
  a local break-glass account as fallback.

**Default**: mark one credential as the org default. Every device without
its own credential uses it. This is the normal setup; you rarely need to
assign credentials per device.

Passwords are never shown again after saving. Delete and re-add a
credential to change its password.

---

## 4. Devices

**Devices** page. This is your inventory.

### Adding devices

- **Add device**: name, host (IP or DNS name), SSH port, device type, and
  optional site. As you type the name, Packrat detects the role
  (core, distribution, access, WLC, firewall, PDU) and the IT/OT zone from
  common naming conventions and shows what it guessed. You can override
  both.
- **Bulk add from hostnames**: paste a list of hostnames, one per line,
  pick a device type and site for the batch, and Packrat creates them all.
  Hosts default to the hostname itself, so make sure your DNS resolves
  them or edit the host afterwards.

### Device types

| Type | Used for | Notes |
|---|---|---|
| Cisco IOS Switch/Router | Catalyst, ISR, older Nexus-like IOS | Full audit command set by default |
| Cisco Nexus (NX-OS) | Nexus 3000/5000/7000/9000 | `show running-config` by default |
| Cisco Catalyst 9800 WLC (IOS-XE) | 9800 controllers | Controller-specific commands |
| Cisco AireOS WLC | 5520, 8540, 3504 | `show run-config` and friends |
| Fortinet FortiGate | FortiOS firewalls | VDOM-aware command set |
| APC Switched PDU | APC NMC PDUs | Section names, not show commands |
| Versa SD-WAN Router (VOS) | Versa appliances | Confirm commands against a real unit |

Picking the wrong type is the most common cause of a "Pattern not
detected" or "failed to enter enable mode" error.

### Per-row actions

- **Edit**: change any field, including a custom command list for just
  this device (overrides everything else).
- **Duplicate**: a new device pre-filled from this one, handy for stacks
  of similar switches.
- **History**: every stored config for this device, and the diff tool
  (section 7).
- **SSH**: opens the Terminal page connected to this device (section 11).
- **Delete**: removes the device. Its past jobs and snapshots are kept,
  showing "deleted device".

### Reachability

**Check reachability** pings every device and marks each row. A device
that does not answer ping is not necessarily down; many networks block
ICMP. Use it as a hint, not a verdict.

---

## 5. Commands

**Commands** page. Choose what runs against each device type by default.

Each device type has a card listing the commands Packrat sends. Tick
suggested commands or type your own, comma-separated, then save. The order
of precedence at collection time is:

1. A one-time override you type when starting a job (highest).
2. The device's own custom command list (set in Edit device).
3. The org-wide list saved on this page.
4. Packrat's built-in default for the type (lowest).

Keep `term len 0` (or the platform equivalent) at the top of any Cisco
list so output is not paginated. The built-in defaults already do this.

---

## 6. Jobs: collecting configs

### Starting a collection

On **Devices**, tick devices and press **Collect selected**, or press
**Collect all**. The start dialog shows:

- **Commands per device type**: prefilled from the Commands page. Edit
  here for a one-time override; it does not change the saved profile.
- **One-time passcodes**: appears only if a credential in use has passcode
  MFA. Enter the current code from your token.

Press **Start collection**. You are taken to the job page.

### Reading a job

The **Jobs** page lists every job with its status. Open one to see:

- A **status bar** showing how many devices are pending, authenticating,
  running, completed, failed or cancelled.
- One row per device with its status and, if it failed, a plain-language
  error explaining what went wrong and what to check.
- **Console** on each row shows the live transcript: connecting,
  authenticated, each command and its output. Consoles auto-expand while a
  job is running.
- **View config** opens the stored snapshot once a device completes.

Statuses:

| Status | Meaning |
|---|---|
| Pending | Queued, not started |
| Authenticating | Logging in. If your credential uses push MFA, approve it now |
| Running | Logged in, commands are executing |
| Completed | Config stored |
| Failed | See the error column. Retry is available |
| Cancelled | You cancelled the job before this device ran |

Devices run in a pipeline: as soon as one device has authenticated, the
next starts logging in. At most one device is in the authentication step
at a time, which keeps TACACS+ servers happy.

### Retrying, cancelling, rerunning

- **Retry** on a failed device runs it again inside the same job, with a
  chance to change commands or supply a fresh passcode. The job's other
  results are untouched.
- **Cancel job** stops devices that have not started and interrupts the
  ones in progress between commands.
- On the **Jobs** list, tick old jobs and **Rerun** to collect the same
  devices again as a new job.
- **Clear all finished jobs** deletes job records (snapshots are kept).

### Downloading

On a job page, pick a **file type** (.txt, .cfg, .log), optionally tick
**Add timestamp to filename**, and press **Download all** for a zip with
one file per device. Single configs can be downloaded from **View
config**.

### Untracked neighbors

After a collection that included `show cdp neighbor` or `show lldp
neighbor`, press **Check for untracked neighbors**. Packrat parses the
neighbor tables and lists every neighbor hostname that is not a device in
your inventory, with which device saw it and on which port. Add the ones
you own.

---

## 7. History and diffs

Every completed collection stores a **snapshot** per device. On
**Devices**, press **History** on a row to see them all, newest first.

- **View** opens a snapshot.
- Tick any two and press **Compare selected** for a unified diff. Removed
  lines are red, added lines green, with a few lines of context around
  each change.

Snapshots are kept forever unless you set a retention period in Settings.

---

## 8. Schedules

**Schedules** page (admins). A schedule is a collection that starts itself.

**Add schedule** and choose how it repeats:

| Repeat | You pick | Example |
|---|---|---|
| Daily | Time of day | Every night at 02:00 |
| Weekly | Weekday and time | Sundays at 04:30 |
| Monthly | Day of month and time | The 31st at 23:00 (shorter months run on their last day) |
| One time | Date and time | Before a change window, then it marks itself Done |
| Every N hours | An interval | Every 6 hours |

Times are in your browser's timezone and stored with the schedule, so a
02:00 backup stays at 02:00 local through daylight saving changes. The
timezone is shown next to the time and in the table.

**Devices**: all devices in the org (including ones added later), or a
fixed set you tick.

Each run creates an ordinary job on the Jobs page. Scheduled runs are
unattended, so a device whose credential needs a one-time passcode will
fail on them; collect those by hand.

Per row: **Edit**, **Run now** (starts the job immediately without moving
the next scheduled time), **Delete**, and an **Enabled** checkbox to pause.
A finished one-time schedule shows **Done** with the time it ran; edit it
with a new date to arm it again.

Schedules need the scheduler process running (see section 1). If nothing
fires at the expected time, check that window is still open.

---

## 9. Firmware push

**Firmware** page. Packrat copies an image file onto a device's storage.
It does not install it, change the boot variable, or reload. The upgrade
itself stays a manual, scheduled step you control.

### Upload an image

**Upload image** with an optional label such as the version. Images are
stored on the Packrat server's disk, not in the database.

### Push to devices

Press **Push to devices** on an image and:

1. Tick the target devices. They are handled strictly one at a time.
2. Choose the **transfer protocol**: TFTP, FTP or SCP. Packrat runs its own
   temporary file server for the duration of each device's copy, serving
   only that one file. TFTP and FTP use their standard ports (69 and 21),
   which on most systems require the backend to run elevated; SCP uses
   port 2222 by default and does not.
3. Choose the **network interface**: which of the Packrat machine's IP
   addresses the devices should connect back to. On a laptop with VPN and
   Wi-Fi this matters; pick the one the devices can reach.
4. Review the **copy command per device type**. Cisco IOS, NX-OS and 9800
   are prefilled (`copy {url} flash:`, `copy {url} bootflash: vrf
   management`, `copy {url} bootflash:`). Other types have no safe default
   and need a command from you. `{url}` becomes the complete URL for the
   chosen protocol; `{host}`, `{port}`, `{protocol}` and `{filename}` are
   also available.
5. **Push file**.

During the copy Packrat answers the device's interactive prompts:
destination filename and overwrite are accepted, SSH host-key and yes/no
prompts get "yes", and Cisco's "Erase flash: before copying?" is always
answered **no**. The full dialogue streams into the device's console on
the job page. A vendor error line (timed out, no such file, not enough
space) fails that device with the line as its error.

For NX-OS the default assumes management traffic uses the `management`
VRF. If your Nexus reaches Packrat in-band, remove `vrf management` from
the command in the dialog.

---

## 10. DNS Check

**DNS Check** page. Paste hostnames or IPs, one per line or
comma-separated, and press **Run checks**. For each target Packrat reports:

- **Ping**: reachable or no reply.
- **Forward DNS**: the addresses the name resolves to, or "no DNS record
  found". Shown as N/A when the target is already an IP.
- **Reverse DNS**: the hostname from the PTR record, or "no PTR record".

Checks run as a background job with a progress bar, so a list of several
thousand targets is fine. Past checks are listed below the form and can be
reopened, cancelled or cleared.

As with reachability on the Devices page, a failed ping usually means a
firewall dropped ICMP, not that the device is down.

---

## 11. Terminal

**Terminal** page, or the **SSH** button on any device row. An interactive
SSH session in the browser using the device's credential (or the org
default), including MFA handling. If the credential uses passcode MFA, a
field appears for the current code.

Press **Connect**. The terminal resizes with the window. Nothing you type
is stored by Packrat. Press **Disconnect** or close the tab to end the
session.

---

## 12. Settings

**Settings** page (admins).

- **Snapshot retention**: number of days to keep stored configs. Older
  snapshots are deleted once an hour by the scheduler process. Leave empty
  to keep everything forever.

---

## 13. Troubleshooting

**"Could not establish an SSH session ... TCP connection to device failed"**
The device is unreachable from the Packrat machine: wrong IP, DNS name,
firewall, VPN not connected, or SSH not enabled on port 22. Raising the
auth timeout does not help this. Try the Terminal page to confirm.

**"Authentication was rejected"**
Wrong username or password, or a passcode-MFA credential without a fresh
code. Packrat already retries with keyboard-interactive and then the
fallback credential before reporting this.

**"Failed to enter enable mode"**
Missing or wrong enable secret, or the device type expects enable mode
when the device does not (an AireOS controller set up as a 9800, for
example). Fix the type or the secret.

**Login works in Terminal but the job times out at Authenticating**
Push MFA is waiting for approval. Approve on your phone, and raise the
credential's auth timeout so the next run waits long enough.

**Commands run but output looks like it belongs to the previous command**
Wrong device type for a WLC or PDU. Set it to the matching type; those use
timing-based reads that cope with slow, chatty devices.

**Scheduled backups never run**
The scheduler ("beat") process is not running. On Windows it is the
`run-beat.ps1` window; with Docker Compose it is the `beat` service.

**Firmware push: "Timed out waiting for the copy to finish"**
The device could not reach Packrat's file server. Check the network
interface you picked, that the port is not blocked, and for TFTP/FTP that
the backend has permission to bind ports 69 and 21.

**Windows: pip install fails with a `.tmp` file error**
Two run scripts installed dependencies at the same time. Close the windows
and start `run-dev.ps1` again; installs are now serialized.

**Times in the UI look shifted by several hours**
Update to the current version. Earlier builds sent timestamps without a
timezone marker.

---

## 14. Security notes

- Device passwords and enable secrets are encrypted at rest with your
  `CREDENTIAL_ENCRYPTION_KEY` and decrypted only in memory for the length
  of an SSH session.
- One-time passcodes are used for the run they were entered for and never
  stored.
- Transcripts and snapshots may contain sensitive configuration (SNMP
  strings, hashed passwords). Treat the Packrat database and downloads
  accordingly; set a retention period if policy requires it.
- The firmware file server is bound to one interface, serves one file,
  accepts any login, and is torn down when the copy finishes. Do not point
  it at an interface facing an untrusted network.
- Every API call is scoped to the caller's organization. Set a strong
  `JWT_SECRET_KEY` before exposing the API beyond localhost.
