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
11. [SNMP](#11-snmp)
12. [Terminal](#12-terminal)
13. [Settings](#13-settings)
14. [Troubleshooting](#14-troubleshooting)
15. [Security notes](#15-security-notes)

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
   This runs the backend, the worker, the scheduler and the frontend
   together in the window you typed it in. Every line says which process
   wrote it. Press Ctrl+C in that window to stop everything. The first run
   installs dependencies and takes a few minutes; later runs skip that
   when nothing changed.
5. The frontend opens in your browser at http://localhost:5173.

Prefer them apart? `.\run-dev.ps1 -Panes` opens one Windows Terminal tab
split into four panes, and `.\run-dev.ps1 -Windows` gives each process its
own window. To run without Memurai (jobs then run inside the backend and
"Start collection" waits until they finish), use `.\run-dev.ps1 -Eager`.

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
  (core, distribution, access, WLC, firewall, PDU) and the device type
  from your organization's naming rules and shows what it guessed. You can
  override both. The rules themselves are yours to define; see
  [Device naming rules](#device-naming-rules) under Settings.
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

Not in the list? Add your own type (Juniper, Arista, Palo Alto, a Linux
host, a console server) on the **Commands** page; see
[Your own device types](#your-own-device-types). It then appears in this
dropdown under "Your device types".

### Per-row actions

- **Edit**: change any field, including a custom command list for just
  this device (overrides everything else).
- **Duplicate**: a new device pre-filled from this one, handy for stacks
  of similar switches.
- **History**: every stored config for this device, and the diff tool
  (section 7).
- **SSH**: opens the Terminal page connected to this device (section 12).
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

### Your own device types

The top of the **Commands** page lists the device types you have defined
yourself, for any platform the built-in list does not cover. **Add device
type** asks for:

- **Name**: what you will see in dropdowns, e.g. "Juniper SRX firewall".
- **Key**: a short identifier stored on devices (`juniper_srx`). It is
  filled in from the name and cannot change once saved.
- **Netmiko driver**: the CLI dialect Packrat should speak. Start typing to
  search the list: `juniper_junos`, `arista_eos`, `paloalto_panos`,
  `hp_procurve`, `linux`, `generic_termserver` and about a hundred more.
  Use `linux` for anything with a shell and `generic_termserver` for a
  plain prompt.
- **Category**: switch, router, firewall, wireless controller, PDU,
  server, console server or other. Only used for grouping.
- **Commands to run**: one per line. This is where log collection goes:
  `show log messages | last 500` on Junos, `show logging` on Arista,
  `show log system` on PAN-OS, `journalctl -n 500 --no-pager` on Linux.
  Put the platform's paging-off command first (`set cli screen-length 0`,
  `terminal length 0`, `set cli pager off`).
- **Read output by waiting for the channel to go quiet**: leave ticked for
  any platform you have not proven here. Untick only if you know Netmiko's
  prompt detection works for the driver, which makes reads faster.
- **Has an enable mode**: tick for platforms that need the credential's
  enable secret after login.

A custom type behaves like a built-in one everywhere: it appears in the Add
device and bulk-add dropdowns, jobs and schedules collect it, its output is
stored as snapshots and diffed, and the same precedence rules apply (a
device's own custom commands or a one-time override still win). Edit a type
to change its commands for every device using it. A type in use by devices
cannot be deleted until those devices are changed or removed.

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

## 11. SNMP

**SNMP** page. Poll devices over SNMP for what they know about themselves,
without logging in over SSH. Each poll produces one plain-text report per
device containing:

- **System**: sysName, sysDescr (platform and software version),
  sysObjectID, uptime, contact and location.
- **Interfaces**: every interface with its admin and operational status,
  alias, and input/output error and discard counters. Interfaces with
  errors stand out immediately.
- **Syslog history**: the device's own log buffer via Cisco's
  CISCO-SYSLOG-MIB, the same messages `show logging` prints, rendered as
  `%FACILITY-SEVERITY-NAME: text`. On Cisco IOS the buffer must be
  enabled with `logging history <size>` and `logging history <level>`.
  Devices without this MIB simply report that it is not available.
- **Extra OIDs**: anything else you name, walked and listed.

### SNMP profiles

A profile is how Packrat talks SNMP to a device. **Add profile** and pick a
version:

- **SNMPv2c**: a community string. Stored encrypted and never shown again.
- **SNMPv3**: a username plus a **security level**. Use *authPriv*
  (authenticated and encrypted) wherever the device supports it. For
  authNoPriv and authPriv choose the **authentication protocol**
  (SHA256, SHA, SHA224, SHA384, SHA512 or MD5) and its password; for
  authPriv also the **privacy protocol** (AES128, AES192, AES256, 3DES or
  DES) and its password. The protocols and passwords must match what is
  configured on the device for that user. A **context name** is rarely
  needed; leave it blank unless the device documentation says otherwise.
- **UDP port**, **timeout** and **retries**: the defaults (161, 3 seconds,
  1 retry) suit most networks. Lower the timeout if you poll many devices
  that may be offline.

Mark one profile as the **org default**; it is used for every device that
has no profile of its own. Assign a different profile to a specific device
with **Edit** on the Devices page. Editing a profile with a blank password
or community keeps the current secret.

### Polling

Under **Poll devices**, tick the devices, optionally choose a **profile for
this run** to use for every device regardless of their own assignment,
optionally add **extra OIDs** (numeric, one per line, for example
`1.3.6.1.4.1.9.9.109.1.1.1.1.7` for Cisco CPU utilisation), and press
**Poll**. Devices are polled several at a time and the job page updates
as they complete. Open a device's **Report** to read it in place, or
**Download** it as a text file.

A device that does not answer fails with "No SNMP response". For SNMPv2c a
wrong community string looks identical to an unreachable device, because
the device simply stays silent. SNMPv3 problems are reported more
precisely: wrong authentication password, wrong privacy password, or an
unknown user.

### Alerts by email

The **Alerts** section of the SNMP page turns polling into monitoring.
When enabled, Packrat re-polls the chosen devices every few minutes,
remembers what it saw, and emails you when something changes. The first
poll of a device only records a baseline; alerts start with the first
change after that.

Events you can alert on:

| Event | Fires when |
|---|---|
| Link down | An interface that was up, and is not administratively shut, goes down |
| Link up | A down interface comes back |
| Access point down | An AP disappears from its controller's AP table or stops being associated (Cisco WLCs, via the AIRESPACE-WIRELESS-MIB) |
| Access point up | An AP joins or rejoins |
| Device unreachable | A device that answered SNMP before stops answering |
| Device reachable | It answers again |
| Syslog | A new message appears in the device's syslog history at the chosen Cisco level (0 emergencies to 7 debugging) or worse |

Fill in the **Email** section: recipient addresses (comma-separated), the
SMTP server, port and security (STARTTLS on 587, SSL on 465, or none for
an internal relay), an optional username and password, and an optional
From address. The password is stored encrypted and never shown again.
**Send test email** confirms the saved settings work before you rely on
them. **Run a cycle now** polls immediately, which is also how you record
the baseline without waiting for the timer.

All events found in one cycle go into a single email, grouped by device,
with a subject such as "[Packrat] 3 alerts: 2 link down, 1 access point
down". Every event is also kept in the **Alert history** table with
whether its email was sent, so a broken SMTP setting is visible rather
than silent.

Monitoring runs in the scheduler process ("beat"), the same one that runs
Schedules. If alerts stop, check that window is open.

---

## 12. Terminal

**Terminal** page, or the **SSH** button on any device row. Interactive
SSH sessions in the browser using each device's credential (or the org
default), including MFA handling. If the credential uses passcode MFA, a
field appears for the current code.

Pick a device and press **Connect**. To work on several devices at once,
pick the next device and press **Open in new tab**: every session gets
its own tab along the top of the terminal, and each stays connected while
you type in another. The tab's dot shows its state (amber connecting,
green connected, grey disconnected). Opening the same device twice gives
it a numbered tab, for example `HQ-CORE-SW01 (2)`.

- Click a tab to switch to it; the arrow keys move between tabs when one
  has focus.
- The **×** on a tab (or a middle click) closes that session. **Close
  all** ends every session.
- **Disconnect** and **Reconnect** in the toolbar act on the tab you are
  looking at. A reconnect keeps the earlier output above and, for
  passcode MFA, asks for a fresh code.

The terminal resizes with the window. Nothing you type is stored by
Packrat. Leaving the page closes every session.

---

## 13. Settings

**Settings** page (admins).

- **Snapshot retention**: number of days to keep stored configs. Older
  snapshots are deleted once an hour by the scheduler process. Leave empty
  to keep everything forever.

### Device naming rules

Every organization names devices differently. One embeds role codes like
`SWA` (access switch) and `SWC` (core), as in `GBGYSP01SWA001`; another
spells it out, as in `den-core-sw01` or `plant2-acc-03`. The **Device
naming rules** table on the Settings page tells Packrat how to read *your*
names when it pre-fills role and device type on Add device and bulk add.

Each rule says: when the name **contains** / **starts with** / **ends
with** / **matches regex** this pattern, it is this **role** and
optionally this **device type**. A rule may set either or both. Rules run
top to bottom, and for role and type separately the first matching rule
wins, so one rule can supply the role and another the type for the same
name. Matching is case insensitive.

- The built-in convention is shown until you save your own. Edit it in
  place, or remove those rows and add yours, then **Save rules**.
- **Custom role…** lets a rule introduce a role not in the built-in list
  (for example `wan_edge`, labelled "WAN edge router"). It then appears in
  the Role dropdown on Add device and in the Devices table.
- Device type can be any built-in or custom device type, so a rule can
  say "`-leaf-` means an Arista EOS access switch".
- **Try a hostname** shows, as you type, what the rules above make of a
  name, including edits you have not saved yet.
- **Reset to built-in** discards your rules and returns to the original
  convention. Only admins can change rules; everyone can see them.

Explicit values always win: whatever you pick on the Add device form is
used even if a rule would have guessed differently.

### Troubleshooting (log files)

Packrat writes everything it does to log files on the server: every
request and who made it, every device it connects to and each command it
runs there, every scheduled run, SNMP poll and alert email, and every
error with its full traceback. Errors in the browser are sent to the same
log, so one bundle covers the whole application.

The **Troubleshooting** panel at the bottom of Settings (admins) shows:

- The log files and their sizes. There is one per process, so you can see
  at a glance whether the worker or scheduler is running at all:
  `packrat-api.log` (web server), `packrat-worker.log` (collections,
  pushes, polls, alerts) and `packrat-beat.log` (scheduler). Each file
  rotates at 10 MB and keeps five older copies (`.1` to `.5`).
- A live view of the last 100 to 2000 lines of any file, with **Warnings
  and errors only** to hide routine traffic and **Follow** to refresh
  every five seconds while you reproduce a problem.
- **Download log bundle**: a zip of every log file plus a `system-info.txt`
  describing the server (Python version, platform, database type). Send
  this with your report.

When a page shows *Internal server error (ref 3f9c2a1b)*, search the log
for that reference code: the traceback that caused it is on the same line.

The files live in the `logs` folder next to the backend (`LOG_DIR` to move
them). `LOG_LEVEL=DEBUG` adds successful page loads, SQL and SSH handshake
detail; leave it at the default `INFO` otherwise. Passwords, SNMP
communities and one-time codes are never written to the log. On a shared
server hosting several organizations set `LOG_DOWNLOAD_ENABLED=false` so
logs are only readable from the server itself, since they cover every
organization.

---

## 14. Troubleshooting

**Start here: get the logs**
Settings → Troubleshooting → **Download log bundle** collects every log
file. Reproduce the problem first with **Follow** ticked on the API or
worker log and you will usually see the cause as it happens. See section
13 for what each file contains.

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

**SNMP alerts never arrive**
Check the Alert history: "not sent" with an SMTP error means the mail
server settings are wrong; use Send test email to iterate. No alerts at
all usually means the scheduler ("beat") process is not running, or the
change happened before the baseline was recorded.

**Scheduled backups never run**
The scheduler ("beat") process is not running. On Windows it is the
`run-beat.ps1` window; with Docker Compose it is the `beat` service.

**Firmware push: "Timed out waiting for the copy to finish"**
The device could not reach Packrat's file server. Check the network
interface you picked, that the port is not blocked, and for TFTP/FTP that
the backend has permission to bind ports 69 and 21.

**SNMP poll: "No SNMP response (timed out)"**
The device has SNMP disabled, your Packrat machine is not in its SNMP
access list, UDP 161 is blocked, or (for v2c) the community string is
wrong. Test from the Packrat machine with `snmpwalk` or a similar tool
using the same settings.

**Windows: pip install fails with a `.tmp` file error**
Two run scripts installed dependencies at the same time. Close the windows
and start `run-dev.ps1` again; installs are now serialized.

**Times in the UI look shifted by several hours**
Update to the current version. Earlier builds sent timestamps without a
timezone marker.

---

## 15. Security notes

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
