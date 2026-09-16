import { useMemo, useState } from "react";

import { extractErrorMessage } from "../api/client";
import { jobsApi } from "../api/resources";
import type { Credential, Device, DeviceType, JobDetail } from "../api/types";
import { formatLocalDateTime } from "../utils/formatDate";

interface RetryContext {
  jobId: string;
  itemId: string;
}

interface Props {
  devices: Device[];
  deviceTypes: DeviceType[];
  credentials: Credential[];
  onClose: () => void;
  onStarted: (job: JobDetail) => void;
  // When set, this modal retries one existing job item in place instead of
  // starting a brand new job - `devices` must be exactly that one device.
  retryContext?: RetryContext;
}

export function StartCollectionModal({ devices, deviceTypes, credentials, onClose, onStarted, retryContext }: Props) {
  const deviceTypeMap = useMemo(() => new Map(deviceTypes.map((t) => [t.key, t])), [deviceTypes]);
  const credentialMap = useMemo(() => new Map(credentials.map((c) => [c.id, c])), [credentials]);
  const orgDefaultCredential = useMemo(() => credentials.find((c) => c.is_default), [credentials]);

  // Group the target devices by device type so we can ask "which command(s)
  // for each device (switch, pdu, wlc, firewall...)" - grouped by category
  // for readability, but the actual override is per exact device type since
  // e.g. Cisco IOS and Juniper Junos switches don't share command syntax.
  const typesPresent = useMemo(() => {
    const counts = new Map<string, number>();
    for (const d of devices) counts.set(d.device_type, (counts.get(d.device_type) ?? 0) + 1);
    return Array.from(counts.entries())
      .map(([key, count]) => ({ key, count, spec: deviceTypeMap.get(key) }))
      .sort((a, b) => (a.spec?.category ?? "").localeCompare(b.spec?.category ?? "") || a.key.localeCompare(b.key));
  }, [devices, deviceTypeMap]);

  const [commandsByType, setCommandsByType] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const { key, spec } of typesPresent) {
      initial[key] = spec?.default_commands.join(", ") ?? "";
    }
    return initial;
  });

  // Any credential in use by the target devices that requires a one-time
  // passcode needs it supplied fresh for this run - it's never stored.
  // This includes fallback credentials: which one ends up authenticating a
  // device isn't known until it's actually contacted, so a passcode-based
  // fallback needs its code up front too, just in case it's needed. A
  // device with no credential_id of its own falls back to the org's
  // default credential, so that counts too.
  const credentialsNeedingOtp = useMemo(() => {
    const ids = new Set<string>();
    for (const d of devices) {
      const primary = (d.credential_id ? credentialMap.get(d.credential_id) : undefined) ?? orgDefaultCredential;
      if (primary) {
        ids.add(primary.id);
        if (primary.fallback_credential_id) ids.add(primary.fallback_credential_id);
      }
    }
    return Array.from(ids)
      .map((id) => credentialMap.get(id))
      .filter((c): c is Credential => !!c && c.mfa_mode === "passcode" && !c.has_totp_secret); // a stored TOTP seed generates the code
  }, [devices, credentialMap, orgDefaultCredential]);

  const [otps, setOtps] = useState<Record<string, string>>({});
  // Shown in the job list instead of an id. Prefilled with the local date
  // and time so every job has a meaningful name even if nobody types one.
  const [name, setName] = useState(() => `Collection ${formatLocalDateTime(new Date())}`);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit() {
    setError(null);
    setSubmitting(true);
    try {
      if (retryContext) {
        const device = devices[0];
        const primaryCred = (device.credential_id ? credentialMap.get(device.credential_id) : undefined) ?? orgDefaultCredential;
        const fallbackCred = primaryCred?.fallback_credential_id
          ? credentialMap.get(primaryCred.fallback_credential_id)
          : undefined;
        const { data } = await jobsApi.retryItem(retryContext.jobId, retryContext.itemId, {
          commands: commandsByType[device.device_type]?.trim() || undefined,
          credential_otp: primaryCred ? otps[primaryCred.id] : undefined,
          fallback_otp: fallbackCred ? otps[fallbackCred.id] : undefined,
        });
        onStarted(data);
      } else {
        const { data } = await jobsApi.create({
          name: name.trim() || undefined,
          deviceIds: devices.map((d) => d.id),
          commandsByDeviceType: commandsByType,
          credentialOtps: otps,
        });
        onStarted(data);
      }
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
          <h2>
            {retryContext
              ? `Retry ${devices[0]?.name ?? "device"}`
              : `Start collection (${devices.length} device${devices.length === 1 ? "" : "s"})`}
          </h2>
          <div className="modal-header-actions">
            <button onClick={onClose}>Cancel</button>
          </div>
        </div>
        <div className="modal-body">
          {!retryContext && (
            <label style={{ display: "block", marginBottom: 14 }}>
              Job name
              <input id="job-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} placeholder="e.g. Pre-change backup, Site B" />
            </label>
          )}
          {error && <div className="error-banner">{error}</div>}

          <p className="page-subtitle" style={{ marginTop: 0 }}>
            Devices are queued and authenticated one at a time before any command runs - if a device's
            credential uses TACACS+/MFA, its login (and any approval you need to give) happens first,
            and commands only run once that succeeds.
          </p>

          <h3>Commands to run, per device type</h3>
          {typesPresent.map(({ key, count, spec }) => (
            <label key={key} style={{ marginBottom: 14 }}>
              {spec?.label ?? key} ({spec?.category ?? "unknown"}) · {count} device{count === 1 ? "" : "s"}
              <input
                value={commandsByType[key] ?? ""}
                onChange={(e) => setCommandsByType((prev) => ({ ...prev, [key]: e.target.value }))}
                placeholder="Comma-separated commands"
              />
              {spec?.requires_custom_command && !commandsByType[key]?.trim() && (
                <span className="field-hint">
                  This device type has no default command - enter one before starting.
                </span>
              )}
            </label>
          ))}

          {credentialsNeedingOtp.length > 0 && (
            <>
              <h3>One-time passcodes</h3>
              <p className="page-subtitle">
                These credentials require a fresh passcode for this run (it's used once and never
                stored).
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
            {retryContext ? (submitting ? "Retrying…" : "Retry") : submitting ? "Starting…" : "Start collection"}
          </button>
        </div>
      </div>
    </div>
  );
}
