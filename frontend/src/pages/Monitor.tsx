import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { pingApi } from "../api/resources";
import type { PingDevice, PingMonitorSettings, PingOverview, PingSample } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { sortByDeviceName } from "../utils/deviceNameSort";

type Filter = "all" | "down" | "up" | "unknown";
type View = "grid" | "table";

const REFRESH_MS = 10_000;

const STATE_LABEL: Record<string, string> = { up: "Up", down: "Down", unknown: "Waiting" };
const STATE_GLYPH: Record<string, string> = { up: "✓", down: "✕", unknown: "…" };

function ago(iso: string | null | undefined, now: number): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

function fmtRtt(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 0.1) return "<0.1 ms";
  return ms < 10 ? `${ms.toFixed(1)} ms` : `${Math.round(ms)} ms`;
}

/** Last hour of checks as thin bars: reply = bar height by latency, miss =
 * full-height in the danger colour. Each bar carries its own tooltip. */
function Sparkline({ samples }: { samples: PingSample[] }) {
  const max = Math.max(1, ...samples.map((s) => s.rtt_ms ?? 0));
  return (
    <div className="monitor-spark" role="img" aria-label={`${samples.length} checks in the last hour`}>
      {samples.length === 0 && <span className="monitor-spark-empty">no checks yet</span>}
      {samples.map((s, i) => {
        const h = s.ok ? Math.max(15, Math.round(((s.rtt_ms ?? 0) / max) * 100)) : 100;
        const when = new Date(s.t).toLocaleTimeString();
        return (
          <span
            key={i}
            className={`monitor-spark-bar ${s.ok ? "ok" : "miss"}`}
            style={{ height: `${h}%` }}
            title={s.ok ? `${when}: ${fmtRtt(s.rtt_ms)}` : `${when}: no reply`}
          />
        );
      })}
    </div>
  );
}

function StatTile({ label, value, tone, hint }: { label: string; value: string; tone?: "up" | "down" | "unknown" | "neutral"; hint?: string }) {
  return (
    <div className={`monitor-tile monitor-tile-${tone ?? "neutral"}`} title={hint}>
      <span className="monitor-tile-label">{label}</span>
      <span className="monitor-tile-value">{value}</span>
    </div>
  );
}

export function Monitor() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [data, setData] = useState<PingOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [view, setView] = useState<View>("grid");
  const [search, setSearch] = useState("");
  const [running, setRunning] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    try {
      const { data } = await pingApi.overview();
      setData(data);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => {
      void load();
      setNow(Date.now());
    }, REFRESH_MS);
    const clock = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      window.clearInterval(timer);
      window.clearInterval(clock);
    };
  }, [load]);

  async function runNow() {
    setRunning(true);
    try {
      const { data } = await pingApi.runNow();
      setData(data);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setRunning(false);
    }
  }

  const devices = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    const rank: Record<string, number> = { down: 0, unknown: 1, up: 2 };
    const list = data.devices.filter((d) => (filter === "all" || d.state === filter) && (!q || d.name.toLowerCase().includes(q) || d.host.toLowerCase().includes(q) || (d.site ?? "").toLowerCase().includes(q)));
    const byName = sortByDeviceName(list, (d) => d.name);
    return [...byName].sort((a, b) => rank[a.state] - rank[b.state]);
  }, [data, filter, search]);

  if (error && !data) return <div className="page error-banner">{error}</div>;
  if (!data) return <div className="page">Loading…</div>;

  const { summary, settings } = data;
  const healthy = summary.total > 0 && summary.down === 0 && summary.unknown === 0;

  return (
    <div className="page page-wide">
      <div className="page-header-row">
        <div>
          <h1>Monitor</h1>
          <p className="page-subtitle" style={{ marginTop: 4 }}>
            Every device is pinged every {settings.interval_seconds}s. A device is marked down after {settings.failure_threshold} missed{" "}
            {settings.failure_threshold === 1 ? "check" : "checks in a row"}
            {settings.alert_on_down ? (settings.email_configured ? ", and you get an alert." : ", and you would get an alert once email, Teams or Slack is set up under Alerts.") : "."}
          </p>
        </div>
        <div className="page-actions">
          <span className="monitor-live" title={settings.last_run_at ? `Last cycle ${new Date(settings.last_run_at).toLocaleTimeString()}` : "No cycle has run yet"}>
            <span className={`monitor-live-dot ${settings.enabled && !summary.stale ? "on" : "off"}`} aria-hidden="true" />
            {!settings.enabled ? "Paused" : summary.stale ? "Not checking" : `Checked ${ago(settings.last_run_at, now)} ago`}
          </span>
          <button type="button" onClick={runNow} disabled={running}>
            {running ? "Checking…" : "Check now"}
          </button>
          {isAdmin && (
            <button type="button" className="button-like" onClick={() => setShowSettings((v) => !v)}>
              {showSettings ? "Hide settings" : "Settings"}
            </button>
          )}
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {settings.enabled && summary.stale && summary.total > 0 && (
        <div className="error-banner">
          {settings.last_run_at
            ? `No check has completed for ${ago(settings.last_run_at, now)} - the scheduler (beat) process is probably not running.`
            : "No check has run yet - the scheduler (beat) process runs them. Start it, or press Check now."}{" "}
          Checks run from the scheduler; in eager mode it isn't started, so use Check now or run <code>run-dev.ps1</code> without <code>-Eager</code>.
        </div>
      )}

      {showSettings && isAdmin && (
        <MonitorSettings
          settings={settings}
          onSaved={(s) => {
            setData({ ...data, settings: s });
            setShowSettings(false);
          }}
        />
      )}

      <div className="monitor-tiles" role="group" aria-label="Fleet status">
        <StatTile label="Up" value={String(summary.up)} tone="up" />
        <StatTile label="Down" value={String(summary.down)} tone={summary.down ? "down" : "neutral"} />
        <StatTile label="Waiting" value={String(summary.unknown)} tone={summary.unknown ? "unknown" : "neutral"} hint="Not enough checks yet to say" />
        <StatTile label="Avg latency" value={fmtRtt(summary.avg_rtt_ms)} hint="Mean round-trip of the devices that are up" />
        <StatTile label="Devices" value={String(summary.total)} />
      </div>

      {summary.total === 0 ? (
        <div className="empty-state" style={{ marginTop: 18 }}>
          Nothing to watch yet. <Link to="/devices">Add devices</Link> and they appear here on the next check.
        </div>
      ) : (
        <>
          <div className="monitor-toolbar">
            <div className="monitor-filters" role="tablist" aria-label="Filter by state">
              {(["all", "down", "up", "unknown"] as Filter[]).map((f) => (
                <button key={f} type="button" role="tab" aria-selected={filter === f} className={`monitor-filter${filter === f ? " active" : ""}`} onClick={() => setFilter(f)}>
                  {f === "all" ? "All" : STATE_LABEL[f]}
                  <span className="monitor-filter-count">{f === "all" ? summary.total : summary[f]}</span>
                </button>
              ))}
            </div>
            <input className="monitor-search" placeholder="Search name, host or site" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search devices" />
            <div className="monitor-view" role="group" aria-label="View">
              <button type="button" className={`monitor-filter${view === "grid" ? " active" : ""}`} onClick={() => setView("grid")}>
                Grid
              </button>
              <button type="button" className={`monitor-filter${view === "table" ? " active" : ""}`} onClick={() => setView("table")}>
                Table
              </button>
            </div>
          </div>

          {healthy && filter === "all" && (
            <p className="monitor-allgood">
              <span className="monitor-glyph up" aria-hidden="true">✓</span> Every device is answering.
            </p>
          )}

          {devices.length === 0 ? (
            <div className="empty-state">No devices match.</div>
          ) : view === "grid" ? (
            <div className="monitor-grid">
              {devices.map((d) => (
                <DeviceCard key={d.device_id} device={d} now={now} />
              ))}
            </div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>State</th>
                  <th>Device</th>
                  <th>Host</th>
                  <th>Site</th>
                  <th>Latency</th>
                  <th>Since</th>
                  <th>Uptime 24h</th>
                  <th>Last hour</th>
                </tr>
              </thead>
              <tbody>
                {devices.map((d) => (
                  <tr key={d.device_id}>
                    <td>
                      <span className={`monitor-state monitor-state-${d.state}`}>
                        <span className="monitor-glyph" aria-hidden="true">{STATE_GLYPH[d.state]}</span> {STATE_LABEL[d.state]}
                      </span>
                    </td>
                    <td>
                      <Link to={`/terminal?device=${d.device_id}`}>{d.name}</Link>
                      {d.console && (
                        <>
                          {" · "}
                          <Link to={`/terminal?device=${d.device_id}&via=console`}>console</Link>
                        </>
                      )}
                    </td>
                    <td>{d.host}</td>
                    <td>{d.site ?? "—"}</td>
                    <td>{fmtRtt(d.last_rtt_ms)}</td>
                    <td>{d.last_change_at ? ago(d.last_change_at, now) : "—"}</td>
                    <td>{d.uptime_24h_pct === null ? "—" : `${d.uptime_24h_pct}%`}</td>
                    <td style={{ width: 160 }}>
                      <Sparkline samples={d.recent} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}

function DeviceCard({ device: d, now }: { device: PingDevice; now: number }) {
  const since = d.last_change_at ? ago(d.last_change_at, now) : null;
  return (
    <div className={`monitor-card monitor-card-${d.state}`}>
      <div className="monitor-card-head">
        <span className={`monitor-state monitor-state-${d.state}`}>
          <span className="monitor-glyph" aria-hidden="true">{STATE_GLYPH[d.state]}</span> {STATE_LABEL[d.state]}
        </span>
        <span className="monitor-card-rtt">{d.state === "up" ? fmtRtt(d.last_rtt_ms) : d.state === "down" ? `${d.consecutive_failures} missed` : "checking"}</span>
      </div>
      <div className="monitor-card-name" title={d.name}>
        {d.name}
      </div>
      <div className="monitor-card-host">
        {d.host}
        {d.site ? ` · ${d.site}` : ""}
      </div>
      <Sparkline samples={d.recent} />
      <div className="monitor-card-foot">
        <span>{d.state === "down" ? `Down for ${since ?? "—"}` : d.state === "up" ? `Up for ${since ?? "—"}` : "Waiting for checks"}</span>
        <span>{d.uptime_24h_pct === null ? "" : `${d.uptime_24h_pct}% · 24h`}</span>
      </div>
      <div className="monitor-card-actions">
        <Link to={`/terminal?device=${d.device_id}`} className="link-button">
          SSH
        </Link>
        {d.console && (
          <Link to={`/terminal?device=${d.device_id}&via=console`} className="link-button" title="Out-of-band console via the console server">
            Console
          </Link>
        )}
        <Link to={`/devices`} className="link-button">
          Device
        </Link>
      </div>
    </div>
  );
}

function MonitorSettings({ settings, onSaved }: { settings: PingMonitorSettings; onSaved: (s: PingMonitorSettings) => void }) {
  const [form, setForm] = useState({
    enabled: settings.enabled,
    interval_seconds: String(settings.interval_seconds),
    failure_threshold: String(settings.failure_threshold),
    timeout_ms: String(settings.timeout_ms),
    alert_on_down: settings.alert_on_down,
    alert_on_up: settings.alert_on_up,
    history_days: String(settings.history_days),
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const { data } = await pingApi.updateSettings({
        enabled: form.enabled,
        interval_seconds: Number(form.interval_seconds),
        failure_threshold: Number(form.failure_threshold),
        timeout_ms: Number(form.timeout_ms),
        alert_on_down: form.alert_on_down,
        alert_on_up: form.alert_on_up,
        history_days: Number(form.history_days),
      });
      onSaved(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="card-form" onSubmit={submit} style={{ marginBottom: 18 }}>
      <h3 style={{ marginTop: 0 }}>Monitor settings</h3>
      {error && <div className="error-banner">{error}</div>}
      <label className="inline-label" style={{ marginBottom: 10 }}>
        <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
        Monitoring on (checks run from the scheduler)
      </label>
      <div className="form-grid">
        <label>
          Check every (seconds, 5 to 3600)
          <input type="number" min={5} max={3600} value={form.interval_seconds} onChange={(e) => setForm({ ...form, interval_seconds: e.target.value })} required />
        </label>
        <label>
          Mark down after (missed checks in a row)
          <input type="number" min={1} max={10} value={form.failure_threshold} onChange={(e) => setForm({ ...form, failure_threshold: e.target.value })} required />
        </label>
        <label>
          Ping timeout (ms)
          <input type="number" min={200} max={5000} step={100} value={form.timeout_ms} onChange={(e) => setForm({ ...form, timeout_ms: e.target.value })} required />
        </label>
        <label>
          Keep history (days)
          <input type="number" min={1} max={90} value={form.history_days} onChange={(e) => setForm({ ...form, history_days: e.target.value })} required />
        </label>
      </div>
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap", marginTop: 10 }}>
        <label className="inline-label">
          <input type="checkbox" checked={form.alert_on_down} onChange={(e) => setForm({ ...form, alert_on_down: e.target.checked })} />
          Alert when a device goes down
        </label>
        <label className="inline-label">
          <input type="checkbox" checked={form.alert_on_up} onChange={(e) => setForm({ ...form, alert_on_up: e.target.checked })} />
          Alert when it comes back
        </label>
      </div>
      <p className="field-hint" style={{ marginTop: 10 }}>
        Alerts are listed and delivered (email, Teams, Slack) as set on the <Link to="/alerts">Alerts</Link> page
        {settings.email_configured ? " (a channel is configured)." : " (no channel configured yet)."}
      </p>
      <div className="page-header-row" style={{ marginTop: 14 }}>
        <button type="submit" disabled={saving} style={{ marginLeft: "auto" }}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}
