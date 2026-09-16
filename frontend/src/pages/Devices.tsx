import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { credentialsApi, deviceRolesApi, devicesApi, deviceTypesApi, snmpApi } from "../api/resources";
import type {
  Credential,
  Device,
  DeviceDetection,
  DeviceImportResult,
  DeviceReachability,
  DeviceRole,
  DeviceType,
  SnmpProfile,
} from "../api/types";
import { BulkAddDevicesModal } from "../components/BulkAddDevicesModal";
import { DeviceHistoryModal } from "../components/DeviceHistoryModal";
import { DeviceTypeOptions } from "../components/DeviceTypeOptions";
import { StartCollectionModal } from "../components/StartCollectionModal";
import { sortByDeviceName } from "../utils/deviceNameSort";

/** Bumps a trailing number in a name by one, preserving zero-padding (e.g.
 * "GBGYSP01SWA001" -> "GBGYSP01SWA002") - the common case when duplicating
 * a device to add the next one in a numbered sequence. Names with no
 * trailing number are returned unchanged, left for manual editing. */
/** The Ping column: a green tick when the last reachability check got a
 * ping reply, a red cross when it did not, a dash before any check. */
function ReachMark({ result, checking }: { result: DeviceReachability | undefined; checking: boolean }) {
  if (checking) {
    return (
      <span className="reach-mark reach-pending" title="Checking…" aria-label="Checking">
        …
      </span>
    );
  }
  if (!result) {
    return (
      <span className="reach-mark reach-pending" title="Not checked yet - use Check reachability" aria-label="Not checked">
        –
      </span>
    );
  }
  if (result.ping_ok) {
    return (
      <span
        className="reach-mark reach-ok"
        title={`Answered ping${result.resolved_ip && result.resolved_ip !== result.host ? ` (${result.resolved_ip})` : ""}`}
        aria-label="Reachable: answered ping"
        role="img"
      >
        ✓
      </span>
    );
  }
  return (
    <span
      className="reach-mark reach-bad"
      title={
        "No ping response" +
        (result.dns_ok === false ? " and the hostname didn't resolve in DNS" : "") +
        " - not necessarily offline: a firewall may block ping while SSH still works."
      }
      aria-label="Not reachable: no ping response"
      role="img"
    >
      ✕
    </span>
  );
}

function incrementTrailingNumber(name: string): string {
  const match = /^(.*?)(\d+)$/.exec(name);
  if (!match) return name;
  const [, prefix, digits] = match;
  const incremented = String(Number(digits) + 1).padStart(digits.length, "0");
  return prefix + incremented;
}

export function Devices() {
  const navigate = useNavigate();
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceTypes, setDeviceTypes] = useState<DeviceType[]>([]);
  const [deviceRoles, setDeviceRoles] = useState<DeviceRole[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [snmpProfiles, setSnmpProfiles] = useState<SnmpProfile[]>([]);
  const [snmpProfileId, setSnmpProfileId] = useState("");
  const [consoleHost, setConsoleHost] = useState("");
  const [consolePort, setConsolePort] = useState("");
  const [consoleProtocol, setConsoleProtocol] = useState<"ssh" | "telnet">("ssh");
  const [consoleCredentialId, setConsoleCredentialId] = useState("");
  const [consoleCommand, setConsoleCommand] = useState("");
  const [showConsole, setShowConsole] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [collectionTarget, setCollectionTarget] = useState<Device[] | null>(null);
  const [historyTarget, setHistoryTarget] = useState<Device | null>(null);
  const [importResult, setImportResult] = useState<DeviceImportResult | null>(null);
  const [showBulkAdd, setShowBulkAdd] = useState(false);
  const [reachability, setReachability] = useState<Map<string, DeviceReachability>>(new Map());
  const [checkingReachability, setCheckingReachability] = useState(false);
  const [reachabilityError, setReachabilityError] = useState<string | null>(null);
  const [reachabilityCheckedAt, setReachabilityCheckedAt] = useState<Date | null>(null);
  const reachableCount = useMemo(() => Array.from(reachability.values()).filter((r) => r.ping_ok).length, [reachability]);
  const [clearingAll, setClearingAll] = useState(false);
  const [clearNotice, setClearNotice] = useState<string | null>(null);

  const [showAddForm, setShowAddForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [deviceType, setDeviceType] = useState("");
  const [deviceRole, setDeviceRole] = useState("");
  const [site, setSite] = useState("");
  const [customCommands, setCustomCommands] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [detection, setDetection] = useState<DeviceDetection | null>(null);

  // Auto-detection (from the name typed above) only ever fills a field the
  // user hasn't touched themselves - once you pick something explicitly,
  // typing more of the name won't overwrite it.
  const typeTouched = useRef(false);
  const roleTouched = useRef(false);
  const nameInputRef = useRef<HTMLInputElement>(null);

  const deviceTypeMap = useMemo(() => new Map(deviceTypes.map((t) => [t.key, t])), [deviceTypes]);
  const deviceRoleMap = useMemo(() => new Map(deviceRoles.map((r) => [r.key, r])), [deviceRoles]);
  // The backend returns devices in whatever order the database happens to
  // store them in (insertion order, roughly) - sorted here by the same
  // role-code + trailing-number convention as the bulk-add review table, so
  // the list reads sensibly regardless of how or in what order devices were
  // actually added.
  const sortedDevices = useMemo(() => sortByDeviceName(devices, (d) => d.name), [devices]);

  async function refresh() {
    setLoading(true);
    try {
      const [devicesRes, typesRes, rolesRes, credsRes] = await Promise.all([
        devicesApi.list(),
        deviceTypesApi.list(),
        deviceRolesApi.list(),
        credentialsApi.list(),
      ]);
      setDevices(devicesRes.data);
      setDeviceTypes(typesRes.data);
      setDeviceRoles(rolesRes.data);
      setCredentials(credsRes.data);
      snmpApi.listProfiles().then((r) => setSnmpProfiles(r.data)).catch(() => setSnmpProfiles([]));
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  // Focus the name field (cursor at the end) whenever the add form opens,
  // so duplicating a device drops you straight into editing the part that
  // actually needs to change (e.g. the trailing "001" -> "002").
  useEffect(() => {
    const el = nameInputRef.current;
    if (showAddForm && el) {
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    }
  }, [showAddForm]);

  // Debounced hostname detection: as the name field settles, ask the
  // backend what device type/role it suggests (see
  // app.services.hostname_detection) and pre-fill anything not already
  // touched by hand.
  useEffect(() => {
    if (!showAddForm || !name.trim()) {
      setDetection(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const { data } = await devicesApi.detect(name.trim());
        if (cancelled) return;
        setDetection(data);
        if (!typeTouched.current && data.suggested_device_type) setDeviceType(data.suggested_device_type);
        if (!roleTouched.current) setDeviceRole(data.device_role ?? "");
      } catch {
        // Best-effort only - a failed detection call shouldn't block adding
        // the device manually.
      }
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [name, showAddForm]);

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    setSelected((prev) => (prev.size === devices.length ? new Set() : new Set(devices.map((d) => d.id))));
  }

  function resetForm() {
    setEditingId(null);
    setName("");
    setHost("");
    setPort("22");
    setDeviceType("");
    setDeviceRole("");
    setSite("");
    setCustomCommands("");
    setSnmpProfileId("");
    setConsoleHost("");
    setConsolePort("");
    setConsoleProtocol("ssh");
    setConsoleCredentialId("");
    setConsoleCommand("");
    setShowConsole(false);
    setDetection(null);
    typeTouched.current = false;
    roleTouched.current = false;
  }

  function loadConsoleFields(device: Device) {
    setConsoleHost(device.console_host ?? "");
    setConsolePort(device.console_port ? String(device.console_port) : "");
    setConsoleProtocol(device.console_protocol ?? "ssh");
    setConsoleCredentialId(device.console_credential_id ?? "");
    setConsoleCommand(device.console_connect_command ?? "");
    setShowConsole(Boolean(device.console_host));
  }

  function handleDuplicate(device: Device) {
    setEditingId(null);
    setName(incrementTrailingNumber(device.name));
    setHost(device.host);
    setPort(String(device.port));
    setDeviceType(device.device_type);
    setDeviceRole(device.device_role ?? "");
    setSite(device.site ?? "");
    setCustomCommands(device.custom_commands ?? "");
    setSnmpProfileId(device.snmp_profile_id ?? "");
    loadConsoleFields(device);
    setDetection(null);
    // Every field just came from an existing device, not a fresh
    // auto-detect guess - mark them all touched so editing the name
    // afterward (e.g. bumping "001" further) can't silently overwrite
    // type/role with a differing detection result.
    typeTouched.current = true;
    roleTouched.current = true;
    setShowAddForm(true);
  }

  function handleEdit(device: Device) {
    setEditingId(device.id);
    setName(device.name);
    setHost(device.host);
    setPort(String(device.port));
    setDeviceType(device.device_type);
    setDeviceRole(device.device_role ?? "");
    setSite(device.site ?? "");
    setCustomCommands(device.custom_commands ?? "");
    setSnmpProfileId(device.snmp_profile_id ?? "");
    loadConsoleFields(device);
    setDetection(null);
    // Editing an existing device shouldn't have typing in the name field
    // re-trigger auto-detection and clobber its current type/role.
    typeTouched.current = true;
    roleTouched.current = true;
    setShowAddForm(true);
  }

  async function handleSubmitDevice(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const payload = {
        name,
        host,
        port: Number(port) || 22,
        device_type: deviceType || undefined,
        device_role: deviceRole || undefined,
        site: site || undefined,
        custom_commands: customCommands || undefined,
        snmp_profile_id: snmpProfileId || undefined,
        console_host: consoleHost.trim() || undefined,
        console_port: consoleHost.trim() && consolePort ? Number(consolePort) : undefined,
        console_protocol: consoleHost.trim() ? consoleProtocol : undefined,
        console_credential_id: consoleHost.trim() && consoleProtocol === "ssh" && consoleCredentialId ? consoleCredentialId : undefined,
        console_connect_command: consoleHost.trim() ? consoleCommand.trim() || undefined : undefined,
      };
      if (editingId) {
        await devicesApi.update(editingId, {
          ...payload,
          ...(snmpProfileId ? {} : { clear_snmp_profile: true }),
          ...(consoleHost.trim() ? {} : { clear_console: true }),
        } as Partial<Device>);
      } else {
        await devicesApi.create(payload);
      }
      resetForm();
      setShowAddForm(false);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this device?")) return;
    try {
      await devicesApi.remove(id);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function handleOpenCollect(targetDevices: Device[]) {
    setCollectionTarget(targetDevices);
  }

  function toggleAddForm() {
    if (showAddForm) resetForm();
    setShowAddForm((v) => !v);
  }

  async function handleCheckReachability() {
    setReachabilityError(null);
    setCheckingReachability(true);
    try {
      const { data } = await devicesApi.checkReachability();
      setReachability(new Map(data.map((r) => [r.device_id, r])));
      setReachabilityCheckedAt(new Date());
    } catch (err) {
      setReachabilityError(extractErrorMessage(err));
    } finally {
      setCheckingReachability(false);
    }
  }

  async function handleClearAll() {
    if (
      !confirm(
        "Delete every device in this org? A device with a collection job in progress is left alone - " +
          "everything else's job history and collected configs are kept (just no longer linked to a " +
          "device), but the devices themselves are gone for good."
      )
    ) {
      return;
    }
    setError(null);
    setClearNotice(null);
    setClearingAll(true);
    try {
      const { data } = await devicesApi.clearAll();
      await refresh();
      setSelected(new Set());
      setReachability(new Map());
      setClearNotice(
        `Deleted ${data.deleted} device(s).` +
          (data.skipped > 0 ? ` ${data.skipped} left alone (collection in progress).` : "")
      );
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setClearingAll(false);
    }
  }

  return (
    <div className="page">
      <div className="page-header-row">
        <h1>Devices</h1>
        <div className="page-actions">
          <button className="button-like" onClick={() => setShowBulkAdd(true)}>
            Bulk add from hostnames
          </button>
          <button className="link-button" onClick={handleCheckReachability} disabled={checkingReachability}>
            {checkingReachability ? "Checking…" : "Check reachability"}
          </button>
          <button
            className="link-button danger"
            onClick={handleClearAll}
            disabled={clearingAll || devices.length === 0}
          >
            {clearingAll ? "Clearing…" : "Clear all devices"}
          </button>
          <button onClick={toggleAddForm}>{showAddForm ? "Cancel" : "Add device"}</button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {clearNotice && <div className="info-banner">{clearNotice}</div>}
      {reachabilityError && <div className="error-banner">{reachabilityError}</div>}
      {reachability.size > 0 && reachabilityCheckedAt && (
        <p className="page-subtitle" style={{ marginTop: 0 }}>
          <strong>
            Checked at {reachabilityCheckedAt.toLocaleTimeString()}: {reachableCount} of {reachability.size} device
            {reachability.size === 1 ? "" : "s"} answered ping
            {reachability.size - reachableCount > 0 ? `, ${reachability.size - reachableCount} did not` : ""}.
          </strong>{" "}
          A cross is a heads-up, not proof the device is offline - a firewall commonly blocks ping for a device
          that's perfectly reachable over SSH, and a name can be missing from DNS for a device that's still
          reachable some other way. Hover a mark for the detail.
        </p>
      )}
      {importResult && (
        <div className="info-banner">
          Imported {importResult.created} device(s).
          {importResult.errors.length > 0 && (
            <ul>
              {importResult.errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {showAddForm && (
        <form className="card-form" onSubmit={handleSubmitDevice}>
          {editingId && <p className="field-hint" style={{ marginTop: 0 }}>Editing {name || "device"}</p>}
          <div className="form-grid">
            <label>
              Name
              <input ref={nameInputRef} value={name} onChange={(e) => setName(e.target.value)} required />
              {detection && detection.device_role_label && (
                <span className="field-hint">
                  Detected from name: {detection.device_role_label}
                  {!detection.suggested_device_type &&
                    detection.device_role_label &&
                    " — pick a device type below (can't be guessed for this role)."}
                  {" · "}
                  <Link to="/settings#naming">naming rules</Link>
                </span>
              )}
            </label>
            <label>
              Host / IP
              <input value={host} onChange={(e) => setHost(e.target.value)} required />
            </label>
            <label>
              Port
              <input type="number" value={port} onChange={(e) => setPort(e.target.value)} />
            </label>
            <label>
              Device type
              <select
                value={deviceType}
                onChange={(e) => {
                  typeTouched.current = true;
                  setDeviceType(e.target.value);
                }}
              >
                <option value="">— select / auto-detect from name —</option>
                <DeviceTypeOptions deviceTypes={deviceTypes} />
              </select>
            </label>
            <label>
              Role (optional)
              <select
                value={deviceRole}
                onChange={(e) => {
                  roleTouched.current = true;
                  setDeviceRole(e.target.value);
                }}
              >
                <option value="">— none / auto-detect —</option>
                {deviceRoles.map((r) => (
                  <option key={r.key} value={r.key}>
                    {r.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Site (optional)
              <input value={site} onChange={(e) => setSite(e.target.value)} />
            </label>
            {snmpProfiles.length > 0 && (
              <label>
                SNMP profile (optional)
                <select value={snmpProfileId} onChange={(e) => setSnmpProfileId(e.target.value)}>
                  <option value="">— org default{snmpProfiles.some((p) => p.is_default) ? ` (${snmpProfiles.find((p) => p.is_default)!.name})` : ""} —</option>
                  {snmpProfiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.version})
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="form-grid-span console-section">
              <button type="button" className="link-button" onClick={() => setShowConsole((v) => !v)} aria-expanded={showConsole}>
                {showConsole ? "▾" : "▸"} Console access (optional){consoleHost.trim() ? ` · ${consoleProtocol} ${consoleHost.trim()}:${consolePort || (consoleProtocol === "ssh" ? 22 : 23)}` : ""}
              </button>
              {showConsole && (
                <div className="console-fields">
                  <p className="field-hint" style={{ marginTop: 6 }}>
                    Out-of-band path for when the management address is unreachable: the console server (Opengear,
                    Lantronix, Avocent, Digi, a Cisco async line) and the TCP port that maps to this device's serial
                    console. The Terminal and Monitor pages then offer "Connect via console".
                  </p>
                  <div className="form-grid">
                    <label>
                      Console server host
                      <input value={consoleHost} onChange={(e) => setConsoleHost(e.target.value)} placeholder="e.g. oob-cs1.example or 10.99.0.5" />
                    </label>
                    <label>
                      Protocol / port
                      <div style={{ display: "flex", gap: 8 }}>
                        <select value={consoleProtocol} onChange={(e) => setConsoleProtocol(e.target.value as "ssh" | "telnet")} style={{ flex: "0 0 auto" }}>
                          <option value="ssh">SSH</option>
                          <option value="telnet">Telnet (reverse telnet)</option>
                        </select>
                        <input type="number" min={1} max={65535} value={consolePort} onChange={(e) => setConsolePort(e.target.value)} placeholder={consoleProtocol === "ssh" ? "22" : "23 / 2003"} />
                      </div>
                    </label>
                    {consoleProtocol === "ssh" && (
                      <label>
                        Console server login
                        <select value={consoleCredentialId} onChange={(e) => setConsoleCredentialId(e.target.value)}>
                          <option value="">— same as this device —</option>
                          {credentials.map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.name} ({c.username})
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                    <label>
                      Connect command after login (optional)
                      <input value={consoleCommand} onChange={(e) => setConsoleCommand(e.target.value)} placeholder='e.g. "connect line 3" or "pmshell -l port03"' />
                    </label>
                  </div>
                </div>
              )}
            </div>
            <label className="form-grid-span">
              Custom command(s) (comma-separated, overrides default for this device type)
              <input value={customCommands} onChange={(e) => setCustomCommands(e.target.value)} />
              {deviceType && deviceTypeMap.get(deviceType)?.requires_custom_command && (
                <span className="field-hint">
                  This device type has no default command — a custom command is required.
                </span>
              )}
            </label>
          </div>
          <button type="submit" disabled={submitting}>
            {submitting ? "Saving…" : editingId ? "Save changes" : "Save device"}
          </button>
        </form>
      )}

      <div className="page-header-row">
        <div className="selection-actions">
          <button disabled={devices.length === 0} onClick={() => handleOpenCollect(devices)}>
            Collect all
          </button>
          <button
            disabled={selected.size === 0}
            onClick={() => handleOpenCollect(devices.filter((d) => selected.has(d.id)))}
          >
            Collect selected ({selected.size})
          </button>
        </div>
      </div>

      {loading ? (
        <p>Loading…</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  checked={devices.length > 0 && selected.size === devices.length}
                  onChange={toggleSelectAll}
                />
              </th>
              <th>Name</th>
              <th>Host</th>
              <th title="Result of the last Check reachability run: ping answered (tick) or not (cross)">Ping</th>
              <th>Port</th>
              <th>Type</th>
              <th>Role</th>
              <th>Site</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sortedDevices.map((d) => (
              <tr key={d.id}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.has(d.id)}
                    onChange={() => toggleSelected(d.id)}
                  />
                </td>
                <td>{d.name}</td>
                <td>{d.host}</td>
                <td className="reach-cell">
                  <ReachMark result={reachability.get(d.id)} checking={checkingReachability} />
                </td>
                <td>{d.port}</td>
                <td>{deviceTypeMap.get(d.device_type)?.label ?? d.device_type}</td>
                <td>{d.device_role ? (deviceRoleMap.get(d.device_role)?.label ?? d.device_role) : "—"}</td>
                <td>{d.site ?? "—"}</td>
                <td>
                  <button className="link-button" onClick={() => handleEdit(d)}>
                    Edit
                  </button>
                  <button
                    className="link-button"
                    style={{ marginLeft: 12 }}
                    onClick={() => handleDuplicate(d)}
                  >
                    Duplicate
                  </button>
                  <button
                    className="link-button"
                    style={{ marginLeft: 12 }}
                    onClick={() => setHistoryTarget(d)}
                  >
                    History
                  </button>
                  <button
                    className="link-button"
                    style={{ marginLeft: 12 }}
                    onClick={() => navigate(`/terminal?device=${d.id}`)}
                  >
                    SSH
                  </button>
                  {d.console_host && (
                    <button
                      className="link-button"
                      style={{ marginLeft: 12 }}
                      title={`Out of band via ${d.console_protocol} ${d.console_host}:${d.console_port}`}
                      onClick={() => navigate(`/terminal?device=${d.id}&via=console`)}
                    >
                      Console
                    </button>
                  )}
                  <button
                    className="link-button danger"
                    style={{ marginLeft: 12 }}
                    onClick={() => handleDelete(d.id)}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {devices.length === 0 && (
              <tr>
                <td colSpan={9} className="empty-state">
                  No devices yet. Add one, or paste a list of hostnames to bulk add.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {historyTarget && (
        <DeviceHistoryModal device={historyTarget} onClose={() => setHistoryTarget(null)} />
      )}

      {collectionTarget && (
        <StartCollectionModal
          devices={collectionTarget}
          deviceTypes={deviceTypes}
          credentials={credentials}
          onClose={() => setCollectionTarget(null)}
          onStarted={(job) => {
            setCollectionTarget(null);
            navigate(`/jobs/${job.id}`);
          }}
        />
      )}

      {showBulkAdd && (
        <BulkAddDevicesModal
          deviceTypes={deviceTypes}
          deviceRoles={deviceRoles}
          onClose={() => setShowBulkAdd(false)}
          onDone={(result) => {
            setShowBulkAdd(false);
            setImportResult(result);
            void refresh();
          }}
        />
      )}
    </div>
  );
}
