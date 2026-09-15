import { useEffect, useMemo, useState } from "react";

import { extractErrorMessage } from "../api/client";
import { firmwareApi } from "../api/resources";
import type {
  Credential,
  Device,
  DeviceType,
  FirmwareImage,
  FirmwareJobDetail,
  NetworkInterface,
  TransferProtocol,
} from "../api/types";

interface Props {
  image: FirmwareImage;
  devices: Device[];
  deviceTypes: DeviceType[];
  credentials: Credential[];
  onClose: () => void;
  onStarted: (job: FirmwareJobDetail) => void;
}

const PROTOCOLS: { value: TransferProtocol; label: string }[] = [
  { value: "tftp", label: "TFTP" },
  { value: "ftp", label: "FTP" },
  { value: "scp", label: "SCP" },
];

export function StartFirmwareUpgradeModal({ image, devices, deviceTypes, credentials, onClose, onStarted }: Props) {
  const deviceTypeMap = useMemo(() => new Map(deviceTypes.map((t) => [t.key, t])), [deviceTypes]);
  const credentialMap = useMemo(() => new Map(credentials.map((c) => [c.id, c])), [credentials]);
  const orgDefaultCredential = useMemo(() => credentials.find((c) => c.is_default), [credentials]);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const selectedDevices = useMemo(() => devices.filter((d) => selectedIds.has(d.id)), [devices, selectedIds]);

  const [protocol, setProtocol] = useState<TransferProtocol>("tftp");
  const [interfaces, setInterfaces] = useState<NetworkInterface[]>([]);
  const [serverHost, setServerHost] = useState("");
  const [interfacesError, setInterfacesError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await firmwareApi.listNetworkInterfaces();
        if (cancelled) return;
        setInterfaces(data);
        if (data.length > 0) setServerHost((prev) => prev || data[0].address);
      } catch (err) {
        if (!cancelled) setInterfacesError(extractErrorMessage(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const typesPresent = useMemo(() => {
    const counts = new Map<string, number>();
    for (const d of selectedDevices) counts.set(d.device_type, (counts.get(d.device_type) ?? 0) + 1);
    return Array.from(counts.entries())
      .map(([key, count]) => ({ key, count, spec: deviceTypeMap.get(key) }))
      .sort((a, b) => (a.spec?.category ?? "").localeCompare(b.spec?.category ?? "") || a.key.localeCompare(b.key));
  }, [selectedDevices, deviceTypeMap]);

  const [commandsByType, setCommandsByType] = useState<Record<string, string>>({});

  const credentialsNeedingOtp = useMemo(() => {
    const ids = new Set<string>();
    for (const d of selectedDevices) {
      const primary = (d.credential_id ? credentialMap.get(d.credential_id) : undefined) ?? orgDefaultCredential;
      if (primary) ids.add(primary.id);
    }
    return Array.from(ids)
      .map((id) => credentialMap.get(id))
      .filter((c): c is Credential => !!c && c.mfa_mode === "passcode");
  }, [selectedDevices, credentialMap, orgDefaultCredential]);

  const [otps, setOtps] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function toggleDevice(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleSubmit() {
    setError(null);
    if (selectedDevices.length === 0) {
      setError("Select at least one device.");
      return;
    }
    if (!serverHost.trim()) {
      setError("Pick which network interface devices should use to reach this app.");
      return;
    }
    const missingCommands = typesPresent.filter(({ key }) => !commandsByType[key]?.trim());
    if (missingCommands.length > 0) {
      setError(
        "An upgrade command template is required for: " +
          missingCommands.map(({ key, spec }) => spec?.label ?? key).join(", ")
      );
      return;
    }

    setSubmitting(true);
    try {
      const { data } = await firmwareApi.createJob({
        firmware_image_id: image.id,
        device_ids: selectedDevices.map((d) => d.id),
        protocol,
        server_host: serverHost,
        commands_by_device_type: commandsByType,
        credential_otps: otps,
      });
      onStarted(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" style={{ width: "min(900px, 95vw)" }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Upgrade firmware - {image.original_filename}</h2>
          <div className="modal-header-actions">
            <button onClick={onClose}>Cancel</button>
          </div>
        </div>
        <div className="modal-body">
          {error && <div className="error-banner">{error}</div>}

          <p className="page-subtitle" style={{ marginTop: 0 }}>
            Devices are upgraded one at a time, never in parallel - a device fetches the image from
            this app's own transfer server, over whichever protocol you pick below, then runs whatever
            command sequence you supply for its device type. There's no built-in default here (unlike
            config collection's "show" commands) - a wrong upgrade sequence can brick a device, so it's
            always your call.
          </p>

          <h3>Devices</h3>
          <div className="card-form" style={{ maxHeight: 180, overflowY: "auto", marginBottom: 16 }}>
            {devices.length === 0 && <p className="page-subtitle">No devices in this org yet.</p>}
            {devices.map((d) => (
              <label key={d.id} style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <input type="checkbox" checked={selectedIds.has(d.id)} onChange={() => toggleDevice(d.id)} />
                {d.name} ({deviceTypeMap.get(d.device_type)?.label ?? d.device_type})
              </label>
            ))}
          </div>

          <h3>Transfer protocol</h3>
          <label>
            Protocol
            <select value={protocol} onChange={(e) => setProtocol(e.target.value as TransferProtocol)}>
              {PROTOCOLS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>

          <label style={{ marginTop: 14 }}>
            Network interface (which address of this machine devices should use to reach it)
            {interfacesError && <span className="field-hint">{interfacesError}</span>}
            <select value={serverHost} onChange={(e) => setServerHost(e.target.value)}>
              {interfaces.length === 0 && <option value="">No interfaces detected</option>}
              {interfaces.map((i) => (
                <option key={`${i.name}-${i.address}`} value={i.address}>
                  {i.name} — {i.address}
                </option>
              ))}
            </select>
          </label>

          {typesPresent.length > 0 && (
            <>
              <h3 style={{ marginTop: 20 }}>Upgrade commands, per device type (required)</h3>
              <p className="field-hint" style={{ marginTop: 0 }}>
                Reference the transfer server with {"{protocol}"}, {"{host}"}, {"{port}"}, and{" "}
                {"{filename}"} - e.g. "copy {"{protocol}"}://{"{host}"}:{"{port}"}/{"{filename}"} flash:,
                reload".
              </p>
              {typesPresent.map(({ key, count, spec }) => (
                <label key={key} style={{ marginBottom: 14 }}>
                  {spec?.label ?? key} · {count} device{count === 1 ? "" : "s"}
                  <input
                    value={commandsByType[key] ?? ""}
                    onChange={(e) => setCommandsByType((prev) => ({ ...prev, [key]: e.target.value }))}
                    placeholder="Comma-separated commands"
                  />
                </label>
              ))}
            </>
          )}

          {credentialsNeedingOtp.length > 0 && (
            <>
              <h3>One-time passcodes</h3>
              <p className="page-subtitle">
                These credentials require a fresh passcode for this run (used once, never stored).
              </p>
              {credentialsNeedingOtp.map((c) => (
                <label key={c.id} style={{ marginBottom: 14 }}>
                  Passcode for "{c.name}"
                  <input
                    value={otps[c.id] ?? ""}
                    onChange={(e) => setOtps((prev) => ({ ...prev, [c.id]: e.target.value }))}
                    placeholder="e.g. 123456"
                  />
                </label>
              ))}
            </>
          )}
        </div>
        <div className="modal-footer">
          <button onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Starting…" : "Start upgrade"}
          </button>
        </div>
      </div>
    </div>
  );
}
