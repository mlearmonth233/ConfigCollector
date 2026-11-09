# NetTools for Excel

An Excel add-in that adds a **NetTools** tab to the ribbon:

| Button | What it does |
|---|---|
| **Check DNS** | For every IP address or hostname in the selected column: forward lookup, reverse (PTR) lookup, a forward/reverse match check, and a ping |
| **DNS Only** | The lookups without the ping (faster) |
| **Ping Only** | Ping status, latency and TTL |
| **Clear Results** | Removes the NetTools result columns next to the selected column |
| **About** | Short help |

## Using it

1. Put IP addresses and/or hostnames in a column. A header such as
   `Hostname` or `IP Address` is fine; NetTools spots it and skips it.
2. Select the column, a range in it, or just its first cell (NetTools then
   runs down to the last value in the column).
3. Click **Check DNS**. Results go into the columns to the right. NetTools
   asks before it overwrites anything, and it can insert new columns
   instead. Press **Esc** to stop a long run.

Results for **Check DNS**:

| Column | Example |
|---|---|
| Input Type | `Hostname` or `IP` |
| Resolved IP(s) | `10.1.2.3, fe80::1` (IPv4 first) or `(not found)` |
| Reverse DNS (PTR) | `sw-core-01.corp.example.com` or `(no PTR record)` |
| Fwd/Rev Match | `Yes`, `No PTR`, or `No - ...` with the reason |
| Ping | `Up`, `Timed out`, `Destination host unreachable`, ... |
| Latency (ms) | `3` |
| TTL | `254` |

Ping and match cells are coloured green, red or yellow.

**Fwd/Rev Match** means:
- for a **hostname**: the PTR record of its first address is the same name
  (`server1` matches `server1.corp.example.com`);
- for an **IP**: its PTR name resolves back to that same IP.

Lookups go through the Windows resolver, so the answers match what the PC
itself sees (DNS suffixes, hosts file and so on). Ping uses WMI, so no
command windows pop up. Each ping waits up to 1 second
(`PING_TIMEOUT_MS` in `src/NetTools.bas`), and hosts are checked one at a
time, so a list of hosts that are down takes about a second each.

It also adds worksheet functions you can use in formulas:

```
=NT_RESOLVE(A2)    10.1.2.3
=NT_PTR(A2)        server1.corp.example.com
=NT_PING(A2)       Up (3 ms)
```

## Install

### MSI (recommended)

Double-click `NetTools-1.0.0.msi` and close Excel first if it's open. No admin
rights are needed. The installer puts `NetTools.xlam` in your Excel startup
folder (`%APPDATA%\Microsoft\Excel\XLSTART`). Excel loads it every time it
starts, and because that folder is one of Office's default trusted
locations, the macros run without a security prompt.

- Uninstall: **Settings > Apps > NetTools for Excel**.
- Upgrade: run a newer MSI. It replaces the old version.
- Silent / Intune / SCCM: `msiexec /i NetTools-1.0.0.msi /qn`. This is a
  **per-user** MSI, so deploy it in the *user* context.

If your company uses Group Policy to turn off trusted locations or block
all macros, the ribbon tab will appear but its buttons won't work. Ask IT
to allow the add-in, for example by signing it with the company's
code-signing certificate.

### Manually (no MSI)

Copy `NetTools.xlam` to `%APPDATA%\Microsoft\AddIns`, then in Excel go to
**File > Options > Add-ins > Manage: Excel Add-ins > Go...**, click
**Browse**, and pick the file. If you downloaded the file, right-click it
first, choose **Properties**, and tick **Unblock**. Otherwise Windows marks
it as coming from the internet and Excel blocks its macros.

## Building

The `.xlam` holds compiled VBA, so you have to build it on **Windows with
desktop Excel installed**:

```powershell
cd excel-addin\NetTools
powershell -ExecutionPolicy Bypass -File .\build.ps1          # dist\NetTools.xlam
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Msi     # + dist\NetTools-<version>.msi
```

- The script drives Excel in the background. It switches on *Trust access to
  the VBA project object model* for the build and then puts the setting
  back the way it was.
- `-Msi` uses the [WiX Toolset](https://wixtoolset.org/) v5. If WiX isn't
  installed, the script installs it as a .NET global tool, which needs the
  [.NET SDK](https://dotnet.microsoft.com/download) 6 or later.
- The version comes from `VERSION`. Bump it before you build an upgrade,
  and keep `APP_VERSION` in `src/NetTools.bas` the same.

### Files

| File | Purpose |
|---|---|
| `src/NetTools.bas` | All the VBA: ribbon callbacks, DNS (ws2_32), ping (WMI), worksheet functions |
| `src/customUI.xml` | Ribbon tab definition |
| `installer/NetTools.wxs` | WiX source for the per-user MSI |
| `build.ps1` | Builds the `.xlam` and, with `-Msi`, the MSI |
| `VERSION` | Version used for the add-in and the MSI |

To change the ribbon, edit `src/customUI.xml`. Each `onAction` names a
`Public Sub ...(control As IRibbonControl)` in `NetTools.bas`.
