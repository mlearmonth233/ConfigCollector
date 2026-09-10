import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { saveBlobResponse } from "../api/download";
import { credentialsApi, deviceRolesApi, devicesApi, deviceTypesApi } from "../api/resources";
import type {
  Credential,
  Device,
  DeviceDetection,
  DeviceImportResult,
  DeviceRole,
  DeviceType,
  NetworkZone,
} from "../api/types";
import { BulkAddDevicesModal } from "../components/BulkAddDevicesModal";
import { DeviceHistoryModal } from "../components/DeviceHistoryModal";
import { StartCollectionModal } from "../components/StartCollectionModal";

const ZONE_LABELS: Record<NetworkZone, string> = { it: "IT", ot: "OT" };

/** Bumps a trailing number in a name by one, preserving zero-padding (e.g.
 * "GBGYSP01SWA001" -> "GBGYSP01SWA002") - the common case when duplicating
 * a device to add the next one in a numbered sequence. Names with no
 * trailing number are returned unchanged, left for manual editing. */
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [collectionTarget, setCollectionTarget] = useState<Device[] | null>(null);
  const [historyTarget, setHistoryTarget] = useState<Device | null>(null);
  const [importResult, setImportResult] = useState<DeviceImportResult | null>(null);
  const [showBulkAdd, setShowBulkAdd] = useState(false);

  const [showAddForm, setShowAddForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [deviceType, setDeviceType] = useState("");
  const [deviceRole, setDeviceRole] = useState("");
  const [networkZone, setNetworkZone] = useState<"" | NetworkZone>("");
  const [site, setSite] = useState("");
  const [customCommands, setCustomCommands] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [detection, setDetection] = useState<DeviceDetection | null>(null);

  // Auto-detection (from the name typed above) only ever fills a field the
  // user hasn't touched themselves - once you pick something explicitly,
  // typing more of the name won't overwrite it.
  const typeTouched = useRef(false);
  const roleTouched = useRef(false);
  const zoneTouched = useRef(false);
  const nameInputRef = useRef<HTMLInputElement>(null);

  const deviceTypeMap = useMemo(() => new Map(deviceTypes.map((t) => [t.key, t])), [deviceTypes]);
  const deviceRoleMap = useMemo(() => new Map(deviceRoles.map((r) => [r.key, r])), [deviceRoles]);

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
  // backend what device type/role/zone it suggests (see
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
        if (!zoneTouched.current) setNetworkZone(data.network_zone ?? "");
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
    setNetworkZone("");
    setSite("");
    setCustomCommands("");
    setDetection(null);
    typeTouched.current = false;
    roleTouched.current = false;
    zoneTouched.current = false;
  }

  function handleDuplicate(device: Device) {
    setEditingId(null);
    setName(incrementTrailingNumber(device.name));
    setHost(device.host);
    setPort(String(device.port));
    setDeviceType(device.device_type);
    setDeviceRole(device.device_role ?? "");
    setNetworkZone((device.network_zone ?? "") as "" | NetworkZone);
    setSite(device.site ?? "");
    setCustomCommands(device.custom_commands ?? "");
    setDetection(null);
    // Every field just came from an existing device, not a fresh
    // auto-detect guess - mark them all touched so editing the name
    // afterward (e.g. bumping "001" further) can't silently overwrite
    // type/role/zone with a differing detection result.
    typeTouched.current = true;
    roleTouched.current = true;
    zoneTouched.current = true;
    setShowAddForm(true);
  }

  function handleEdit(device: Device) {
    setEditingId(device.id);
    setName(device.name);
    setHost(device.host);
    setPort(String(device.port));
    setDeviceType(device.device_type);
    setDeviceRole(device.device_role ?? "");
    setNetworkZone((device.network_zone ?? "") as "" | NetworkZone);
    setSite(device.site ?? "");
    setCustomCommands(device.custom_commands ?? "");
    setDetection(null);
    // Editing an existing device shouldn't have typing in the name field
    // re-trigger auto-detection and clobber its current type/role/zone.
    typeTouched.current = true;
    roleTouched.current = true;
    zoneTouched.current = true;
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
        network_zone: (networkZone || undefined) as NetworkZone | undefined,
        site: site || undefined,
        custom_commands: customCommands || undefined,
      };
      if (editingId) {
        await devicesApi.update(editingId, payload);
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

  async function handleDownloadTemplate() {
    setError(null);
    try {
      const response = await devicesApi.downloadImportTemplate();
      saveBlobResponse(response, "device_import_template.csv");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleImport(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    setImportResult(null);
    try {
      const { data } = await devicesApi.importCsv(file);
      setImportResult(data);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      e.target.value = "";
    }
  }

  function handleOpenCollect(targetDevices: Device[]) {
    setCollectionTarget(targetDevices);
  }

  function toggleAddForm() {
    if (showAddForm) resetForm();
    setShowAddForm((v) => !v);
  }

  return (
    <div className="page">
      <div className="page-header-row">
        <h1>Devices</h1>
        <div className="page-actions">
          <button className="link-button" onClick={handleDownloadTemplate}>
            Download CSV template
          </button>
          <label className="button-like">
            Import CSV
            <input type="file" accept=".csv" onChange={handleImport} hidden />
          </label>
          <button className="button-like" onClick={() => setShowBulkAdd(true)}>
            Bulk add from hostnames
          </button>
          <button onClick={toggleAddForm}>{showAddForm ? "Cancel" : "Add device"}</button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}
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
              {detection && (detection.device_role_label || detection.network_zone) && (
                <span className="field-hint">
                  Detected from name: {detection.device_role_label ?? "unknown role"}
                  {detection.network_zone ? ` · ${ZONE_LABELS[detection.network_zone]}` : ""}
                  {!detection.suggested_device_type &&
                    detection.device_role_label &&
                    " — pick a device type below (can't be guessed for this role)."}
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
                {deviceTypes.map((t) => (
                  <option key={t.key} value={t.key}>
                    {t.label}
                  </option>
                ))}
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
              Network zone (optional)
              <select
                value={networkZone}
                onChange={(e) => {
                  zoneTouched.current = true;
                  setNetworkZone(e.target.value as "" | NetworkZone);
                }}
              >
                <option value="">— none / auto-detect —</option>
                <option value="it">IT</option>
                <option value="ot">OT</option>
              </select>
            </label>
            <label>
              Site (optional)
              <input value={site} onChange={(e) => setSite(e.target.value)} />
            </label>
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
              <th>Port</th>
              <th>Type</th>
              <th>Role</th>
              <th>Zone</th>
              <th>Site</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {devices.map((d) => (
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
                <td>{d.port}</td>
                <td>{deviceTypeMap.get(d.device_type)?.label ?? d.device_type}</td>
                <td>{d.device_role ? (deviceRoleMap.get(d.device_role)?.label ?? d.device_role) : "—"}</td>
                <td>{d.network_zone ? ZONE_LABELS[d.network_zone] : "—"}</td>
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
                  No devices yet. Add one or import a CSV.
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
