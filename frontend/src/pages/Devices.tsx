import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { credentialsApi, devicesApi, deviceTypesApi } from "../api/resources";
import type { Credential, Device, DeviceImportResult, DeviceType } from "../api/types";
import { StartCollectionModal } from "../components/StartCollectionModal";

export function Devices() {
  const navigate = useNavigate();
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceTypes, setDeviceTypes] = useState<DeviceType[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [collectionTarget, setCollectionTarget] = useState<Device[] | null>(null);
  const [importResult, setImportResult] = useState<DeviceImportResult | null>(null);

  const [showAddForm, setShowAddForm] = useState(false);
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [deviceType, setDeviceType] = useState("");
  const [site, setSite] = useState("");
  const [credentialId, setCredentialId] = useState("");
  const [customCommands, setCustomCommands] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const deviceTypeMap = useMemo(() => new Map(deviceTypes.map((t) => [t.key, t])), [deviceTypes]);

  async function refresh() {
    setLoading(true);
    try {
      const [devicesRes, typesRes, credsRes] = await Promise.all([
        devicesApi.list(),
        deviceTypesApi.list(),
        credentialsApi.list(),
      ]);
      setDevices(devicesRes.data);
      setDeviceTypes(typesRes.data);
      setCredentials(credsRes.data);
      if (!deviceType && typesRes.data.length > 0) setDeviceType(typesRes.data[0].key);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  async function handleAddDevice(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await devicesApi.create({
        name,
        host,
        port: Number(port) || 22,
        device_type: deviceType,
        site: site || undefined,
        credential_id: credentialId || undefined,
        custom_commands: customCommands || undefined,
      });
      setName("");
      setHost("");
      setPort("22");
      setSite("");
      setCustomCommands("");
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

  return (
    <div className="page">
      <div className="page-header-row">
        <h1>Devices</h1>
        <div className="page-actions">
          <label className="button-like">
            Import CSV
            <input type="file" accept=".csv" onChange={handleImport} hidden />
          </label>
          <button onClick={() => setShowAddForm((v) => !v)}>{showAddForm ? "Cancel" : "Add device"}</button>
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
        <form className="card-form" onSubmit={handleAddDevice}>
          <div className="form-grid">
            <label>
              Name
              <input value={name} onChange={(e) => setName(e.target.value)} required />
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
              <select value={deviceType} onChange={(e) => setDeviceType(e.target.value)} required>
                {deviceTypes.map((t) => (
                  <option key={t.key} value={t.key}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Credential
              <select value={credentialId} onChange={(e) => setCredentialId(e.target.value)}>
                <option value="">— none —</option>
                {credentials.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
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
            {submitting ? "Saving…" : "Save device"}
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
              <th>Site</th>
              <th>Credential</th>
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
                <td>{d.site ?? "—"}</td>
                <td>{credentials.find((c) => c.id === d.credential_id)?.name ?? "—"}</td>
                <td>
                  <button className="link-button danger" onClick={() => handleDelete(d.id)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {devices.length === 0 && (
              <tr>
                <td colSpan={8} className="empty-state">
                  No devices yet. Add one or import a CSV.
                </td>
              </tr>
            )}
          </tbody>
        </table>
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
    </div>
  );
}
