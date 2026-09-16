import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { alertsApi } from "../api/resources";
import type { AlertChannel, AlertSettings, AlertTestResult, SnmpAlert, SnmpAlertKind } from "../api/types";
import { useAuth } from "../context/AuthContext";

const CHANNEL_LABELS: Record<AlertChannel, string> = { email: "Email", teams: "Teams", slack: "Slack" };

type Source = "all" | "ping" | "snmp" | "config";

const SOURCE_OF_KIND: Record<SnmpAlertKind, Exclude<Source, "all">> = {
  ping_down: "ping",
  ping_up: "ping",
  config_changed: "config",
  link_down: "snmp",
  link_up: "snmp",
  ap_down: "snmp",
  ap_up: "snmp",
  device_down: "snmp",
  device_up: "snmp",
  syslog: "snmp",
};

function kindBadgeClass(kind: SnmpAlertKind): string {
  if (kind.endsWith("_up")) return "status-completed";
  if (kind === "syslog" || kind === "config_changed") return "status-fallback";
  return "status-failed";
}

/** One page that owns where alerts go (email, Microsoft Teams, Slack) and
 * lists every alert any monitor raised - ping, SNMP or a config change. */
export function Alerts() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [settings, setSettings] = useState<AlertSettings | null>(null);
  const [alerts, setAlerts] = useState<SnmpAlert[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<AlertTestResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<"test" | "clear" | null>(null);
  const [source, setSource] = useState<Source>("all");
  const [search, setSearch] = useState("");

  // form state
  const [recipients, setRecipients] = useState("");
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState("587");
  const [smtpUser, setSmtpUser] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [clearPassword, setClearPassword] = useState(false);
  const [smtpSecurity, setSmtpSecurity] = useState<"starttls" | "ssl" | "none">("starttls");
  const [smtpFrom, setSmtpFrom] = useState("");
  const [teamsUrl, setTeamsUrl] = useState("");
  const [removeTeams, setRemoveTeams] = useState(false);
  const [slackUrl, setSlackUrl] = useState("");
  const [removeSlack, setRemoveSlack] = useState(false);
  const [configChange, setConfigChange] = useState(true);

  function applySettings(s: AlertSettings) {
    setSettings(s);
    setRecipients(s.recipients.join(", "));
    setSmtpHost(s.smtp_host ?? "");
    setSmtpPort(String(s.smtp_port));
    setSmtpUser(s.smtp_username ?? "");
    setSmtpPassword("");
    setClearPassword(false);
    setSmtpSecurity(s.smtp_ssl ? "ssl" : s.smtp_starttls ? "starttls" : "none");
    setSmtpFrom(s.smtp_from ?? "");
    setTeamsUrl("");
    setRemoveTeams(false);
    setSlackUrl("");
    setRemoveSlack(false);
    setConfigChange(s.alert_config_change);
  }

  async function refresh() {
    try {
      const [s, list] = await Promise.all([alertsApi.settings(), alertsApi.list()]);
      applySettings(s.data);
      setAlerts(list.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useEffect(() => {
    void refresh();
    const handle = setInterval(() => {
      alertsApi.list().then((r) => setAlerts(r.data)).catch(() => undefined);
    }, 30000);
    return () => clearInterval(handle);
  }, []);

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setTestResult(null);
    setSaving(true);
    try {
      const { data } = await alertsApi.updateSettings({
        recipients: recipients.split(/[,\n;]+/).map((s) => s.trim()).filter(Boolean),
        smtp_host: smtpHost || null,
        smtp_port: Number(smtpPort) || 587,
        smtp_username: smtpUser || null,
        smtp_password: smtpPassword || undefined,
        clear_smtp_password: clearPassword,
        smtp_starttls: smtpSecurity === "starttls",
        smtp_ssl: smtpSecurity === "ssl",
        smtp_from: smtpFrom || null,
        teams_webhook_url: removeTeams ? "-" : teamsUrl.trim() || null,
        slack_webhook_url: removeSlack ? "-" : slackUrl.trim() || null,
        alert_config_change: configChange,
      });
      applySettings(data);
      setNotice(
        data.channels.length > 0
          ? `Saved. Alerts go out by ${data.channels.map((c) => CHANNEL_LABELS[c]).join(", ")}.`
          : "Saved. No delivery channel is set up yet - alerts are recorded here but not sent anywhere.",
      );
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setError(null);
    setNotice(null);
    setTestResult(null);
    setBusy("test");
    try {
      const { data } = await alertsApi.test();
      if (Object.keys(data.results).length === 0) {
        setError("Nothing to test yet - save recipients and an SMTP server, or a Teams or Slack webhook, first.");
      } else {
        setTestResult(data);
      }
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function handleClear() {
    if (!confirm("Clear the whole alert history?")) return;
    setBusy("clear");
    try {
      await alertsApi.clear();
      setAlerts([]);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return alerts.filter((a) => {
      if (source !== "all" && SOURCE_OF_KIND[a.kind] !== source) return false;
      if (!q) return true;
      return `${a.device_name} ${a.subject} ${a.detail ?? ""} ${a.kind_label}`.toLowerCase().includes(q);
    });
  }, [alerts, source, search]);

  const counts = useMemo(() => {
    const c = { all: alerts.length, ping: 0, snmp: 0, config: 0 };
    for (const a of alerts) c[SOURCE_OF_KIND[a.kind] ?? "snmp"] += 1;
    return c;
  }, [alerts]);

  if (!settings) return <div className="page">{error ? <div className="error-banner">{error}</div> : <p>Loading…</p>}</div>;

  return (
    <div className="page page-wide">
      <div className="page-header-row">
        <div>
          <h1>Alerts</h1>
          <p className="page-subtitle" style={{ marginTop: 4 }}>
            Everything Packrat noticed, in one place: devices that stopped answering ping (<Link to="/monitor">Monitor</Link>), links, access
            points and syslog events from <Link to="/snmp">SNMP monitoring</Link>, and configs that changed between backups. Set below where
            those alerts are sent.
          </p>
        </div>
        <div className="page-actions" style={{ flexWrap: "wrap", justifyContent: "flex-end" }}>
          {(["email", "teams", "slack"] as AlertChannel[]).map((c) => (
            <span key={c} className={`status-badge ${settings.channels.includes(c) ? "status-completed" : "status-fallback"}`}>
              {CHANNEL_LABELS[c]} {settings.channels.includes(c) ? "on" : "off"}
            </span>
          ))}
        </div>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="info-banner">{notice}</div>}
      {testResult && (
        <div className={testResult.ok ? "info-banner" : "error-banner"}>
          {testResult.ok ? "Test message sent on every channel:" : "Test message sent - some channels failed:"}
          <ul>
            {(Object.entries(testResult.results) as [AlertChannel, string][]).map(([channel, result]) => (
              <li key={channel}>
                <strong>{CHANNEL_LABELS[channel]}</strong>: {result === "ok" ? "delivered" : result}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ---- history ---- */}
      <div className="page-header-row" style={{ marginTop: 18 }}>
        <h2 style={{ margin: 0 }}>Alert history</h2>
        <div className="page-actions" style={{ flexWrap: "wrap", justifyContent: "flex-end", minWidth: 0 }}>
          <select value={source} onChange={(e) => setSource(e.target.value as Source)} style={{ width: "auto" }} aria-label="Filter by source">
            <option value="all">All sources ({counts.all})</option>
            <option value="ping">Ping monitor ({counts.ping})</option>
            <option value="snmp">SNMP monitoring ({counts.snmp})</option>
            <option value="config">Config changes ({counts.config})</option>
          </select>
          <input
            id="alert-search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search device or detail"
            style={{ width: 220, maxWidth: "100%", minWidth: 0, flex: "0 1 220px" }}
          />
          {alerts.length > 0 && isAdmin && (
            <button className="link-button danger" onClick={handleClear} disabled={busy !== null}>
              Clear history
            </button>
          )}
        </div>
      </div>
      <table className="data-table" style={{ marginTop: 8 }}>
        <thead>
          <tr>
            <th>When</th>
            <th>Device</th>
            <th>Event</th>
            <th>Detail</th>
            <th>Sent via</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((a) => {
            const problem = [a.email_error, a.webhook_error].filter(Boolean).join("; ");
            return (
              <tr key={a.id}>
                <td style={{ whiteSpace: "nowrap" }}>{new Date(a.created_at).toLocaleString()}</td>
                <td>{a.device_name}</td>
                <td>
                  <span className={`status-badge ${kindBadgeClass(a.kind)}`}>{a.kind_label}</span>
                </td>
                <td>{a.detail ?? a.subject}</td>
                <td style={{ whiteSpace: "nowrap" }} title={problem}>
                  {a.notified_via ? a.notified_via.split(",").map((c) => CHANNEL_LABELS[c.trim() as AlertChannel] ?? c.trim()).join(", ") : null}
                  {problem && (
                    <span className="field-hint" style={{ display: "block" }}>
                      {a.notified_via ? "partly failed" : "not sent"} - {problem.slice(0, 70)}
                      {problem.length > 70 ? "…" : ""}
                    </span>
                  )}
                  {!a.notified_via && !problem && <span className="field-hint">not sent</span>}
                </td>
              </tr>
            );
          })}
          {visible.length === 0 && (
            <tr>
              <td colSpan={5} className="empty-state">
                {alerts.length === 0 ? "No alerts yet. Turn on the ping monitor or SNMP monitoring and this fills in as things change." : "Nothing matches that filter."}
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {/* ---- delivery settings ---- */}
      <div className="page-header-row" style={{ marginTop: 32 }}>
        <h2 style={{ margin: 0 }}>Where alerts are sent</h2>
      </div>
      <p className="page-subtitle" style={{ marginTop: 6 }}>
        Every monitor uses these. Fill in whichever you use - email, a Microsoft Teams channel, a Slack channel, or all three.
        {!isAdmin && " Only an admin can change them."}
      </p>
      <form className="card-form" onSubmit={handleSave} style={{ marginTop: 12 }}>
        <fieldset disabled={!isAdmin} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <h3 style={{ marginTop: 0 }}>Email</h3>
          <div className="form-grid">
            <label className="form-grid-span">
              Send alerts to (comma-separated email addresses)
              <input id="alert-recipients" value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="noc@example.com, oncall@example.com" />
            </label>
            <label>
              SMTP server
              <input id="alert-smtp-host" value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} placeholder="smtp.office365.com" />
            </label>
            <label>
              Port / security
              <div style={{ display: "flex", gap: 8 }}>
                <input id="alert-smtp-port" type="number" min={1} max={65535} value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)} style={{ width: 90 }} />
                <select
                  id="alert-smtp-security"
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
              <input id="alert-smtp-user" value={smtpUser} onChange={(e) => setSmtpUser(e.target.value)} autoComplete="off" />
            </label>
            <label>
              SMTP password{" "}
              {settings.has_smtp_password && !clearPassword && (
                <span className="field-hint">
                  (set - leave blank to keep,{" "}
                  <button type="button" className="link-button" style={{ padding: 0, fontSize: "inherit" }} onClick={() => setClearPassword(true)}>
                    remove
                  </button>
                  )
                </span>
              )}
              {clearPassword && <span className="field-hint">(will be removed on save)</span>}
              <input
                id="alert-smtp-password"
                type="password"
                value={smtpPassword}
                onChange={(e) => {
                  setSmtpPassword(e.target.value);
                  if (e.target.value) setClearPassword(false);
                }}
                autoComplete="new-password"
                placeholder={settings.has_smtp_password && !clearPassword ? "(unchanged)" : ""}
              />
            </label>
            <label>
              From address (optional)
              <input id="alert-smtp-from" value={smtpFrom} onChange={(e) => setSmtpFrom(e.target.value)} placeholder="packrat@example.com" />
            </label>
          </div>

          <h3 style={{ marginTop: 18 }}>Microsoft Teams and Slack</h3>
          <p className="page-subtitle" style={{ marginTop: 0 }}>
            Paste an incoming-webhook URL. In Teams: channel › Workflows › "Post to a channel when a webhook request is received". In Slack: Apps ›
            Incoming Webhooks › Add to a channel. The URL contains a secret, so once saved only its host is shown here.
          </p>
          <div className="form-grid">
            <WebhookField
              id="alert-teams-webhook"
              label="Teams webhook URL"
              host={settings.teams_webhook_host}
              value={teamsUrl}
              remove={removeTeams}
              onChange={(v) => {
                setTeamsUrl(v);
                if (v) setRemoveTeams(false);
              }}
              onRemove={() => setRemoveTeams(true)}
              placeholder="https://….logic.azure.com/workflows/…"
            />
            <WebhookField
              id="alert-slack-webhook"
              label="Slack webhook URL"
              host={settings.slack_webhook_host}
              value={slackUrl}
              remove={removeSlack}
              onChange={(v) => {
                setSlackUrl(v);
                if (v) setRemoveSlack(false);
              }}
              onRemove={() => setRemoveSlack(true)}
              placeholder="https://hooks.slack.com/services/…"
            />
          </div>

          <h3 style={{ marginTop: 18 }}>Config changes</h3>
          <div className="form-grid">
            <label className="checkbox-label form-grid-span" style={{ flexDirection: "row", gap: 8, alignItems: "flex-start" }}>
              <input id="alert-config-change" type="checkbox" checked={configChange} onChange={(e) => setConfigChange(e.target.checked)} style={{ marginTop: 3 }} />
              <span>
                <strong>Alert when a backup finds a changed config.</strong> Each collection compares the running config with the device's previous
                snapshot; the alerts for one job go out as a single message. Compare versions under Devices › History. Ping and SNMP alerts are
                switched on in their own pages.
              </span>
            </label>
          </div>
        </fieldset>

        {isAdmin && (
          <div className="page-header-row" style={{ marginTop: 14 }}>
            <div className="page-actions">
              <button type="button" className="link-button" onClick={handleTest} disabled={busy !== null}>
                {busy === "test" ? "Sending…" : "Send a test alert (saved settings)"}
              </button>
            </div>
            <button type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save delivery settings"}
            </button>
          </div>
        )}
      </form>
    </div>
  );
}

interface WebhookFieldProps {
  id: string;
  label: string;
  host: string | null;
  value: string;
  remove: boolean;
  onChange: (value: string) => void;
  onRemove: () => void;
  placeholder: string;
}

function WebhookField({ id, label, host, value, remove, onChange, onRemove, placeholder }: WebhookFieldProps) {
  return (
    <label>
      {label}{" "}
      {host && !remove && (
        <span className="field-hint">
          (set: {host} - leave blank to keep,{" "}
          <button type="button" className="link-button" style={{ padding: 0, fontSize: "inherit" }} onClick={onRemove}>
            remove
          </button>
          )
        </span>
      )}
      {remove && <span className="field-hint">(will be removed on save)</span>}
      <input id={id} value={value} onChange={(e) => onChange(e.target.value)} placeholder={host && !remove ? "(unchanged)" : placeholder} autoComplete="off" />
    </label>
  );
}
