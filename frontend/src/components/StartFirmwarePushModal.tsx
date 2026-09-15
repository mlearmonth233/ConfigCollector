import { useEffect, useMemo, useState } from "react";

import { extractErrorMessage } from "../api/client";
import { firmwareApi, type PushDefaults } from "../api/resources";
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

export function StartFirmwarePushModal({ image, devices, deviceTypes, credentials, onClose, onStarted }: Props) {
  const deviceTypeMap = useMemo(() => new Map(deviceTypes.map((t) => [t.key, t])), [deviceTypes]);
  const credentialMap = useMemo(() => new Map(credentials.map((c) => [c.id, c])), [credentials]);
  const orgDefaultCredential = useMemo(() => credentials.find((c) => c.is_default), [credentials]);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const selectedDevices = useMemo(() => devices.filter((d) => selectedIds.has(d.id)), [devices, selectedIds]);

  const [protocol, setProtocol] = useState<TransferProtocol>("tftp");
  const [interfaces, setInterfaces] = useState<NetworkInterface[]>([]);
  const [serverHost, setServerHost] = useState("");
  const [interfacesError, setInterfacesError] = useState<string | null>(null);
  const [defaults, setDefaults] = useState<PushDefaults | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [{ data: ifaces }, { data: pushDefaults }] = await Promise.all([
          firmwareApi.listNetworkInterfaces(),
          firmwareApi.getPushDefaults(),
        ]);
        if (cancelled) return;
        setInterfaces(ifaces);
        if (ifaces.length > 0) setServerHost((prev) => prev || ifaces[0].address);
        setDefaults(pushDefaults);
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

  // Prefilled from the server's built-in copy command per device type the
  // first time that type appears in the selection; anything the user has
  // typed (or cleared) is left alone.
  const [commandsByType, setCommandsByType] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!defaults) return;
    setCommandsByType((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const { key } of typesPresent) {
        if (next[key] === undefined && defaults.commands_by_device_type[key] !== undefined) {
          next[key] = defaults.commands_by_device_type[key];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [defaults, typesPresent]);

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
        "A copy command is needed for: " +
          missingCommands.map(({ key, spec }) => spec?.label ?? key).join(", ") +
          " (no built-in default exists for this device type)."
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
          <h2>Push firmware - {image.original_filename}</h2>
          <div className="modal-header-actions">
            <button onClick={onClose}>Cancel</button>
          </div>
        </div>
        <div className="modal-body">
          {error && <div className="error-banner">{error}</div>}

          <p className="page-subtitle" style={{ marginTop: 0 }}>
            This copies the image file onto each device's storage (flash:/bootflash:) and stops there -
            nothing is installed, no boot variable is changed, and nothing reloads. Devices are handled
            one at a time: each pulls the file from this app's own transfer server over the protocol
            you pick, and the copy's interactive prompts (destination filename, overwrite, "erase flash
            before copying?") are answered for you - erase is always declined.
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
              <h3 style={{ marginTop: 20 }}>Copy command, per device type</h3>
              <p className="field-hint" style={{ marginTop: 0 }}>
                Prefilled with the built-in copy command where one exists - edit it if your platform
                stores images somewhere else. {"{url}"} becomes the ready-made {protocol.toUpperCase()} URL
                for this image on the interface above
                {defaults && defaults.placeholders.length > 1
                  ? `; ${defaults.placeholders.filter((p) => p !== "{url}").join(", ")} are also available`
                  : ""}
                . Keep it to copy commands - anything that installs or reloads is on you.
              </p>
              {typesPresent.map(({ key, count, spec }) => (
                <label key={key} style={{ marginBottom: 14 }}>
                  {spec?.label ?? key} · {count} device{count === 1 ? "" : "s"}
                  {defaults && defaults.commands_by_device_type[key] === undefined && (
                    <span className="field-hint"> · no built-in default for this type - enter the command</span>
                  )}
                  <input
                    value={commandsByType[key] ?? ""}
                    onChange={(e) => setCommandsByType((prev) => ({ ...prev, [key]: e.target.value }))}
                    placeholder="e.g. copy {url} flash:"
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
            {submitting ? "Starting…" : "Push file"}
          </button>
        </div>
      </div>
    </div>
  );
}
