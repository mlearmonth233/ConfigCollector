import { useState } from "react";

import { extractErrorMessage } from "../api/client";
import { devicesApi } from "../api/resources";
import type { Credential, Device } from "../api/types";

interface Props {
  device: Device;
  credentials: Credential[];
  onClose: () => void;
  onSaved: (device: Device) => void;
}

export function RetryDeviceModal({ device, credentials, onClose, onSaved }: Props) {
  const [host, setHost] = useState(device.host);
  const [credentialId, setCredentialId] = useState(device.credential_id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit() {
    setError(null);
    if (!host.trim()) {
      setError("Hostname/IP is required.");
      return;
    }
    setSubmitting(true);
    try {
      const { data } = await devicesApi.update(device.id, {
        host: host.trim(),
        credential_id: credentialId || null,
      });
      onSaved(data);
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
          <h2>Retry {device.name}</h2>
          <div className="modal-header-actions">
            <button onClick={onClose}>Cancel</button>
          </div>
        </div>
        <div className="modal-body">
          {error && <div className="error-banner">{error}</div>}

          <p className="page-subtitle" style={{ marginTop: 0 }}>
            If the failure was caused by a wrong hostname/IP or credential, fix it here before
            running this device again - leave things unchanged to just retry as-is.
          </p>

          <label>
            Hostname / IP
            <input value={host} onChange={(e) => setHost(e.target.value)} />
          </label>

          <label style={{ marginTop: 14 }}>
            Credential
            <select value={credentialId} onChange={(e) => setCredentialId(e.target.value)}>
              <option value="">Org default</option>
              {credentials.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="modal-footer">
          <button onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Saving…" : "Save & retry"}
          </button>
        </div>
      </div>
    </div>
  );
}
