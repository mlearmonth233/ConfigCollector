import { useMemo, useState } from "react";

import { extractErrorMessage } from "../api/client";
import { firmwareJobsApi } from "../api/resources";
import type { Credential, Device, FirmwareImage, FirmwareJobDetail } from "../api/types";

interface Props {
  image: FirmwareImage;
  devices: Device[];
  credentials: Credential[];
  onClose: () => void;
  onStarted: (job: FirmwareJobDetail) => void;
}

export function PushFirmwareModal({ image, devices, credentials, onClose, onStarted }: Props) {
  const credentialMap = useMemo(() => new Map(credentials.map((c) => [c.id, c])), [credentials]);
  const orgDefaultCredential = useMemo(() => credentials.find((c) => c.is_default), [credentials]);

  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [targetFilename, setTargetFilename] = useState(image.filename);
  const [verifyChecksum, setVerifyChecksum] = useState(true);
  const [reloadAfter, setReloadAfter] = useState(false);
  const [otps, setOtps] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const filteredDevices = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return devices;
    return devices.filter((d) => d.name.toLowerCase().includes(needle) || d.host.toLowerCase().includes(needle));
  }, [devices, filter]);

  const selectedDevices = useMemo(() => devices.filter((d) => selected.has(d.id)), [devices, selected]);

  const credentialsNeedingOtp = useMemo(() => {
    const ids = new Set<string>();
    for (const d of selectedDevices) {
      const primary = (d.credential_id ? credentialMap.get(d.credential_id) : undefined) ?? orgDefaultCredential;
      if (primary) {
        ids.add(primary.id);
        if (primary.fallback_credential_id) ids.add(primary.fallback_credential_id);
      }
    }
    return Array.from(ids)
      .map((id) => credentialMap.get(id))
      .filter((c): c is Credential => !!c && c.mfa_mode === "passcode");
  }, [selectedDevices, credentialMap, orgDefaultCredential]);

  function toggleDevice(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAllFiltered() {
    setSelected((prev) => {
      const allSelected = filteredDevices.every((d) => prev.has(d.id));
      const next = new Set(prev);
      for (const d of filteredDevices) {
        if (allSelected) next.delete(d.id);
        else next.add(d.id);
      }
      return next;
    });
  }

  async function handleSubmit() {
    setError(null);
    if (selectedDevices.length === 0) {
      setError("Select at least one device to push firmware to.");
      return;
    }
    setSubmitting(true);
    try {
      const { data } = await firmwareJobsApi.create({
        firmwareImageId: image.id,
        deviceIds: selectedDevices.map((d) => d.id),
        targetFilename: targetFilename.trim() || undefined,
        verifyChecksum,
        reloadAfter,
        credentialOtps: otps,
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
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Push "{image.filename}"</h2>
          <div className="modal-header-actions">
            <button onClick={onClose}>Cancel</button>
          </div>
        </div>
        <div className="modal-body">
          {error && <div className="error-banner">{error}</div>}

          <p className="page-subtitle" style={{ marginTop: 0 }}>
            Each device is connected to one at a time (never in parallel), and fully finishes - the
            transfer, an optional checksum verification, and an optional reload - before the next
            device starts.
          </p>

          <label style={{ marginBottom: 14 }}>
            Destination filename (on each device's flash)
            <input value={targetFilename} onChange={(e) => setTargetFilename(e.target.value)} />
          </label>

          <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <input type="checkbox" checked={verifyChecksum} onChange={(e) => setVerifyChecksum(e.target.checked)} />
            Verify checksum after copying ("verify /md5") - recommended
          </label>

          <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
            <input type="checkbox" checked={reloadAfter} onChange={(e) => setReloadAfter(e.target.checked)} />
            Reload the device after a successful push
          </label>
          {reloadAfter && (
            <p className="field-hint" style={{ marginTop: -10 }}>
              This reboots the device once the copy (and any verification) succeeds - make sure the
              target device type actually boots from this image before relying on it for production
              devices.
            </p>
          )}

          <h3>Target devices ({selectedDevices.length} selected)</h3>
          <div className="page-header-row" style={{ marginBottom: 8 }}>
            <input
              placeholder="Filter by name or host..."
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              style={{ maxWidth: 260 }}
            />
            <button className="link-button" onClick={toggleSelectAllFiltered}>
              {filteredDevices.every((d) => selected.has(d.id)) ? "Deselect all shown" : "Select all shown"}
            </button>
          </div>
          <div className="scroll-list">
            {filteredDevices.map((d) => (
              <label key={d.id} className="scroll-list-row">
                <input type="checkbox" checked={selected.has(d.id)} onChange={() => toggleDevice(d.id)} />
                <span>
                  {d.name} <span className="page-subtitle">({d.host} · {d.device_type})</span>
                </span>
              </label>
            ))}
            {filteredDevices.length === 0 && <p className="empty-state">No devices match this filter.</p>}
          </div>

          {credentialsNeedingOtp.length > 0 && (
            <>
              <h3>One-time passcodes</h3>
              <p className="page-subtitle">
                These credentials require a fresh passcode for this push (used once, never stored).
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
          <button onClick={handleSubmit} disabled={submitting || selectedDevices.length === 0}>
            {submitting ? "Starting…" : `Push to ${selectedDevices.length} device${selectedDevices.length === 1 ? "" : "s"}`}
          </button>
        </div>
      </div>
    </div>
  );
}
