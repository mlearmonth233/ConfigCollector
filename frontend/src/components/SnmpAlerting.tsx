import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { snmpMonitorApi } from "../api/resources";
import type { Device, SnmpMonitorConfig, SnmpProfile } from "../api/types";
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

/** SNMP monitoring settings: what to poll, how often, and which changes
 * raise an alert. Where alerts go, and the alert history, live on the
 * Alerts page. */
export function SnmpAlerting({ devices, profiles }: Props) {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [config, setConfig] = useState<SnmpMonitorConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);

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
  }

  useEffect(() => {
    snmpMonitorApi
      .get()
      .then((r) => applyConfig(r.data))
      .catch((err) => setError(extractErrorMessage(err)));
  }, []);

  // Keep the "last cycle" line fresh while monitoring is on.
  useEffect(() => {
    if (!config?.enabled) return;
    const handle = setInterval(() => {
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
      });
      applyConfig(data);
      setNotice(
        data.enabled
          ? `Saved. Monitoring is on - the first cycle records a baseline and alerts start on the next change.${data.channels_configured ? "" : " No delivery channel is set up yet, so alerts are only recorded on the Alerts page."}`
          : "Saved. Monitoring is off.",
      );
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleRunNow() {
    setError(null);
    setNotice(null);
    setRunning(true);
    try {
      const { data } = await snmpMonitorApi.runNow();
      applyConfig(data);
      setNotice(data.last_result ?? "Cycle finished.");
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setRunning(false);
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
        <h2 style={{ margin: 0 }}>Monitoring</h2>
        <div className="page-actions">
          {config.enabled ? (
            <span className="status-badge status-completed">Monitoring on · every {config.interval_minutes} min · {config.monitored_device_count} devices</span>
          ) : (
            <span className="status-badge status-fallback">Monitoring off</span>
          )}
        </div>
      </div>
      <p className="page-subtitle" style={{ marginTop: 6 }}>
        Packrat re-polls your devices over SNMP on a timer, remembers what it saw last time, and alerts you when something changes: a link that
        was up goes down, an access point drops off its controller, a device stops answering, or a new syslog message at the severity you choose
        appears. The first poll of a device only records a baseline. Ports that are administratively shut never alert. Alerts are listed and
        delivered (email, Teams, Slack) as set on the <Link to="/alerts">Alerts</Link> page
        {config.channels_configured ? "." : " - no delivery channel is set up there yet."}
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
                <strong>Enable SNMP monitoring</strong>
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
        </fieldset>

        {isAdmin && (
          <div className="page-header-row" style={{ marginTop: 14 }}>
            <div className="page-actions">
              <button type="button" className="link-button" onClick={handleRunNow} disabled={running}>
                {running ? "Polling…" : "Run a cycle now"}
              </button>
              <Link to="/alerts" className="link-button">
                Alert history and delivery settings
              </Link>
            </div>
            <button type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save monitoring settings"}
            </button>
          </div>
        )}
      </form>
    </>
  );
}
