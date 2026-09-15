import { useEffect, useMemo, useState, type FormEvent } from "react";

import { extractErrorMessage } from "../api/client";
import { snmpMonitorApi } from "../api/resources";
import type { Device, SnmpAlert, SnmpMonitorConfig, SnmpProfile } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { sortByDeviceName } from "../utils/deviceNameSort";

const SYSLOG_LEVELS = [
  [0, "0 - emergencies"],
  [1, "1 - alerts"],
  [2, "2 - critical"],
  [3, "3 - errors"],
  [4, "4 - warnings"],
  [5, "5 - notifications"],
  [6, "6 - informational"],
  [7, "7 - debugging"],
] as const;

interface Props {
  devices: Device[];
  profiles: SnmpProfile[];
}

/** SNMP monitoring + email alerting settings and the alert history. */
export function SnmpAlerting({ devices, profiles }: Props) {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [config, setConfig] = useState<SnmpMonitorConfig | null>(null);
  const [alerts, setAlerts] = useState<SnmpAlert[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<"test" | "run" | "clear" | null>(null);

  // form state
  const [enabled, setEnabled] = useState(false);
  const [interval, setIntervalMinutes] = useState("5");
  const [scope, setScope] = useState<"all" | "specific">("all");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [profileId, setProfileId] = useState("");
  const [linkDown, setLinkDown] = useState(true);
  const [linkUp, setLinkUp] = useState(false);
  const [apDown, setApDown] = useState(true);
  const [apUp, setApUp] = useState(false);
  const [deviceDown, setDeviceDown] = useState(true);
  const [deviceUp, setDeviceUp] = useState(true);
  const [watchSyslog, setWatchSyslog] = useState(false);
  const [syslogLevel, setSyslogLevel] = useState("3");
  const [recipients, setRecipients] = useState("");
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState("587");
  const [smtpUser, setSmtpUser] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [smtpSecurity, setSmtpSecurity] = useState<"starttls" | "ssl" | "none">("starttls");
  const [smtpFrom, setSmtpFrom] = useState("");

  const sortedDevices = useMemo(() => sortByDeviceName(devices, (d) => d.name), [devices]);

  function applyConfig(c: SnmpMonitorConfig) {
    setConfig(c);
    setEnabled(c.enabled);
    setIntervalMinutes(String(c.interval_minutes));
    setScope(c.device_ids ? "specific" : "all");
    setSelectedIds(new Set(c.device_ids ?? []));
    setProfileId(c.snmp_profile_id ?? "");
    setLinkDown(c.alert_link_down);
    setLinkUp(c.alert_link_up);
    setApDown(c.alert_ap_down);
    setApUp(c.alert_ap_up);
    setDeviceDown(c.alert_device_down);
    setDeviceUp(c.alert_device_up);
    setWatchSyslog(c.alert_syslog_max_level !== null);
    setSyslogLevel(String(c.alert_syslog_max_level ?? 3));
    setRecipients(c.recipients.join(", "));
    setSmtpHost(c.smtp_host ?? "");
    setSmtpPort(String(c.smtp_port));
    setSmtpUser(c.smtp_username ?? "");
    setSmtpPassword("");
    setSmtpSecurity(c.smtp_ssl ? "ssl" : c.smtp_starttls ? "starttls" : "none");
    setSmtpFrom(c.smtp_from ?? "");
  }

  async function refresh() {
    try {
      const [cfg, al] = await Promise.all([snmpMonitorApi.get(), snmpMonitorApi.listAlerts()]);
      applyConfig(cfg.data);
      setAlerts(al.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  // Poll the alert list while monitoring is on, so new alerts show up.
  useEffect(() => {
    if (!config?.enabled) return;
    const handle = setInterval(() => {
      snmpMonitorApi.listAlerts().then((r) => setAlerts(r.data)).catch(() => undefined);
      snmpMonitorApi.get().then((r) => setConfig(r.data)).catch(() => undefined);
    }, 30000);
    return () => clearInterval(handle);
  }, [config?.enabled]);

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setSaving(true);
    try {
      const { data } = await snmpMonitorApi.update({
        enabled,
        interval_minutes: Number(interval) || 5,
        device_ids: scope === "specific" ? Array.from(selectedIds) : null,
        snmp_profile_id: profileId || null,
        alert_link_down: linkDown,
        alert_link_up: linkUp,
        alert_ap_down: apDown,
        alert_ap_up: apUp,
        alert_device_down: deviceDown,
        alert_device_up: deviceUp,
        alert_syslog_max_level: watchSyslog ? Number(syslogLevel) : null,
        recipients: recipients.split(/[,\n;]+/).map((s) => s.trim()).filter(Boolean),
        smtp_host: smtpHost || null,
        smtp_port: Number(smtpPort) || 587,
        smtp_username: smtpUser || null,
        smtp_password: smtpPassword || undefined,
        smtp_starttls: smtpSecurity === "starttls",
        smtp_ssl: smtpSecurity === "ssl",
        smtp_from: smtpFrom || null,
      });
      applyConfig(data);
      setNotice(data.enabled ? "Saved. Monitoring is on - the first cycle records a baseline and alerts start on the next change." : "Saved. Monitoring is off.");
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleTestEmail() {
    setError(null);
    setNotice(null);
    setBusy("test");
    try {
      const { data } = await snmpMonitorApi.testEmail();
      (data.ok ? setNotice : setError)(data.message);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function handleRunNow() {
    setError(null);
    setNotice(null);
    setBusy("run");
    try {
      const { data } = await snmpMonitorApi.runNow();
      applyConfig(data);
      setAlerts((await snmpMonitorApi.listAlerts()).data);
      setNotice(data.last_result ?? "Cycle finished.");
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function handleClearAlerts() {
    if (!confirm("Clear the alert history?")) return;
    setBusy("clear");
    try {
      await snmpMonitorApi.clearAlerts();
      setAlerts([]);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  function toggleDevice(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (!config) return <p>Loading…</p>;

  return (
    <>
      <div className="page-header-row" style={{ marginTop: 36 }}>
        <h2 style={{ margin: 0 }}>Alerts</h2>
        <div className="page-actions">
          {config.enabled ? (
            <span className="status-badge status-completed">Monitoring on · every {config.interval_minutes} min · {config.monitored_device_count} devices</span>
          ) : (
            <span className="status-badge status-fallback">Monitoring off</span>
          )}
        </div>
      </div>
      <p className="page-subtitle" style={{ marginTop: 6 }}>
        Packrat re-polls your devices over SNMP on a timer, remembers what it saw last time, and emails you
        when something changes: a link that was up goes down, an access point drops off its controller, a
        device stops answering, or a new syslog message at the severity you choose appears. The first poll of
        a device only records a baseline. Ports that are administratively shut never alert.
      </p>
      {config.last_result && (
        <p className="field-hint">
          Last cycle {config.last_run_at ? new Date(config.last_run_at).toLocaleString() : ""}: {config.last_result}
          {config.enabled && config.next_run_at && ` · next ${new Date(config.next_run_at).toLocaleString()}`}
        </p>
      )}
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="info-banner">{notice}</div>}

      <form className="card-form" onSubmit={handleSave} style={{ marginTop: 12 }}>
        <fieldset disabled={!isAdmin} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <div className="form-grid">
            <label className="checkbox-label" style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              <span>
                <strong>Enable monitoring and email alerts</strong>
              </span>
            </label>
            <label>
              Check every (minutes)
              <input type="number" min={1} max={1440} value={interval} onChange={(e) => setIntervalMinutes(e.target.value)} style={{ maxWidth: 120 }} />
            </label>
            <label>
              Devices to watch
              <select value={scope} onChange={(e) => setScope(e.target.value as "all" | "specific")}>
                <option value="all">All devices (including ones added later)</option>
                <option value="specific">Specific devices</option>
              </select>
            </label>
            <label>
              SNMP profile
              <select value={profileId} onChange={(e) => setProfileId(e.target.value)}>
                <option value="">Each device's own, else the org default</option>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {scope === "specific" && (
            <div className="command-profile-card" style={{ marginTop: 10, maxHeight: 200, overflowY: "auto" }}>
              {sortedDevices.map((d) => (
                <label key={d.id} className="checkbox-label" style={{ flexDirection: "row", gap: 8 }}>
                  <input type="checkbox" checked={selectedIds.has(d.id)} onChange={() => toggleDevice(d.id)} />
                  {d.name} <span className="field-hint">({d.host})</span>
                </label>
              ))}
            </div>
          )}

          <h3 style={{ marginTop: 18 }}>Alert on</h3>
          <div className="form-grid">
            <label className="checkbox-label" style={{ flexDirection: "row", gap: 8 }}>
              <input type="checkbox" checked={linkDown} onChange={(e) => setLinkDown(e.target.checked)} />
              <span>Link down - an interface that was up (and not shut) goes down</span>
            </label>
            <label className="checkbox-label" style={{ flexDirection: "row", gap: 8 }}>
              <input type="checkbox" checked={linkUp} onChange={(e) => setLinkUp(e.target.checked)} />
              <span>Link up - a down interface comes back</span>
            </label>
            <label className="checkbox-label" style={{ flexDirection: "row", gap: 8 }}>
              <input type="checkbox" checked={apDown} onChange={(e) => setApDown(e.target.checked)} />
              <span>Access point down - an AP leaves its controller's AP table or stops being associated (Cisco WLCs)</span>
            </label>
            <label className="checkbox-label" style={{ flexDirection: "row", gap: 8 }}>
              <input type="checkbox" checked={apUp} onChange={(e) => setApUp(e.target.checked)} />
              <span>Access point up - an AP joins or rejoins</span>
            </label>
            <label className="checkbox-label" style={{ flexDirection: "row", gap: 8 }}>
              <input type="checkbox" checked={deviceDown} onChange={(e) => setDeviceDown(e.target.checked)} />
              <span>Device unreachable - a device that answered SNMP before stops answering</span>
            </label>
            <label className="checkbox-label" style={{ flexDirection: "row", gap: 8 }}>
              <input type="checkbox" checked={deviceUp} onChange={(e) => setDeviceUp(e.target.checked)} />
              <span>Device reachable again</span>
            </label>
            <label className="checkbox-label" style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
              <input type="checkbox" checked={watchSyslog} onChange={(e) => setWatchSyslog(e.target.checked)} />
              <span>New syslog messages at level</span>
              <select value={syslogLevel} onChange={(e) => setSyslogLevel(e.target.value)} disabled={!watchSyslog} style={{ width: "auto" }}>
                {SYSLOG_LEVELS.map(([v, label]) => (
                  <option key={v} value={String(v)}>
                    {label}
                  </option>
                ))}
              </select>
              <span>or worse (Cisco syslog history)</span>
            </label>
          </div>

          <h3 style={{ marginTop: 18 }}>Email</h3>
          <div className="form-grid">
            <label className="form-grid-span">
              Send alerts to (comma-separated email addresses)
              <input value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="noc@example.com, oncall@example.com" />
            </label>
            <label>
              SMTP server
              <input value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} placeholder="smtp.office365.com" />
            </label>
            <label>
              Port / security
              <div style={{ display: "flex", gap: 8 }}>
                <input type="number" min={1} max={65535} value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)} style={{ width: 90 }} />
                <select
                  value={smtpSecurity}
                  onChange={(e) => {
                    const v = e.target.value as "starttls" | "ssl" | "none";
                    setSmtpSecurity(v);
                    if (v === "ssl" && smtpPort === "587") setSmtpPort("465");
                    if (v === "starttls" && smtpPort === "465") setSmtpPort("587");
                  }}
                >
                  <option value="starttls">STARTTLS (587)</option>
                  <option value="ssl">SSL/TLS (465)</option>
                  <option value="none">None (internal relay, 25)</option>
                </select>
              </div>
            </label>
            <label>
              SMTP username (optional)
              <input value={smtpUser} onChange={(e) => setSmtpUser(e.target.value)} autoComplete="off" />
            </label>
            <label>
              SMTP password {config.has_smtp_password && <span className="field-hint">(set - leave blank to keep)</span>}
              <input type="password" value={smtpPassword} onChange={(e) => setSmtpPassword(e.target.value)} autoComplete="new-password" placeholder={config.has_smtp_password ? "(unchanged)" : ""} />
            </label>
            <label>
              From address (optional)
              <input value={smtpFrom} onChange={(e) => setSmtpFrom(e.target.value)} placeholder="packrat@example.com" />
            </label>
          </div>
        </fieldset>

        {isAdmin && (
          <div className="page-header-row" style={{ marginTop: 14 }}>
            <div className="page-actions">
              <button type="button" className="link-button" onClick={handleTestEmail} disabled={busy !== null}>
                {busy === "test" ? "Sending…" : "Send test email (saved settings)"}
              </button>
              <button type="button" className="link-button" onClick={handleRunNow} disabled={busy !== null}>
                {busy === "run" ? "Polling…" : "Run a cycle now"}
              </button>
            </div>
            <button type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save alert settings"}
            </button>
          </div>
        )}
      </form>

      <div className="page-header-row" style={{ marginTop: 24 }}>
        <h3 style={{ margin: 0 }}>Alert history</h3>
        {alerts.length > 0 && isAdmin && (
          <button className="link-button danger" onClick={handleClearAlerts} disabled={busy !== null}>
            Clear history
          </button>
        )}
      </div>
      <table className="data-table" style={{ marginTop: 8 }}>
        <thead>
          <tr>
            <th>When</th>
            <th>Device</th>
            <th>Event</th>
            <th>Detail</th>
            <th>Email</th>
          </tr>
        </thead>
        <tbody>
          {alerts.map((a) => (
            <tr key={a.id}>
              <td style={{ whiteSpace: "nowrap" }}>{new Date(a.created_at).toLocaleString()}</td>
              <td>{a.device_name}</td>
              <td>
                <span className={`status-badge status-${a.kind.endsWith("_up") ? "completed" : a.kind === "syslog" ? "fallback" : "failed"}`}>{a.kind_label}</span>
              </td>
              <td>{a.detail ?? a.subject}</td>
              <td>{a.emailed ? "sent" : <span className="field-hint" title={a.email_error ?? ""}>not sent{a.email_error ? ` - ${a.email_error.slice(0, 60)}` : ""}</span>}</td>
            </tr>
          ))}
          {alerts.length === 0 && (
            <tr>
              <td colSpan={5} className="empty-state">
                No alerts yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}
