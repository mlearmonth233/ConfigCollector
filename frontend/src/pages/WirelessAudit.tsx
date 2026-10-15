import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { saveBlobResponse } from "../api/download";
import { credentialsApi, deviceTypesApi, devicesApi } from "../api/resources";
import type { Credential, Device, DeviceType } from "../api/types";
import type { AuditFinding, CheckStatus, CommandCoverage, ControllerAudit, R2OResult, R2OStatus, WirelessAuditReport } from "../api/wirelessAudit";
import { wirelessAuditApi } from "../api/wirelessAudit";
import { StartCollectionModal } from "../components/StartCollectionModal";
import { WirelessStandardEditor } from "../components/WirelessStandardEditor";
import { useAuth } from "../context/AuthContext";
import { formatLocalDateTime } from "../utils/formatDate";

type Tab = "overview" | "r2o" | "findings" | "aps" | "groups" | "wncd" | "clients" | "logs" | "standard" | "coverage";

const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "r2o", label: "Return to Operations" },
  { key: "findings", label: "Findings" },
  { key: "aps", label: "Access points" },
  { key: "groups", label: "AP groups & tags" },
  { key: "wncd", label: "WNCD" },
  { key: "clients", label: "Clients" },
  { key: "logs", label: "Logs" },
  { key: "standard", label: "Standard" },
  { key: "coverage", label: "Coverage" },
];

const WLC_TYPES = new Set(["cisco_wlc", "cisco_wlc_9800"]);
const ROW_LIMIT = 500;

function matches(query: string, ...fields: (string | number | null | undefined)[]): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return fields.some((f) => String(f ?? "").toLowerCase().includes(q));
}

function dash(value: string | number | null | undefined): string {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

export function AuditBadge({ status }: { status: CheckStatus | R2OStatus | "fail" | "warn" | "info" }) {
  const label: Record<string, string> = { no_data: "no data", na: "n/a", skipped: "skipped" };
  return <span className={`audit-badge audit-${status}`}>{label[status] ?? status}</span>;
}

function YesNo({ value, title }: { value: boolean | null; title?: string }) {
  if (value === null) return <span className="table-hint">—</span>;
  return (
    <span className={value ? "audit-ok" : "audit-bad"} title={title}>
      {value ? "✓" : "✗"}
    </span>
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

function ScoreCell({ score }: { score: number | null }) {
  if (score === null) return <span className="table-hint">not collected</span>;
  const tone = score >= 90 ? "good" : score >= 70 ? "fair" : "poor";
  return <span className={`audit-score audit-score-${tone}`}>{score}%</span>;
}

/** Wireless Audit: every 9800 / AireOS controller against the
 * organization's wireless standard and Return-to-Operations checklist,
 * from the latest collected snapshots. */
export function WirelessAudit() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const navigate = useNavigate();
  const [report, setReport] = useState<WirelessAuditReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [withDns, setWithDns] = useState(true);
  const [tab, setTab] = useState<Tab>("overview");
  const [query, setQuery] = useState("");
  const [controller, setController] = useState("");
  const [severity, setSeverity] = useState<"" | "fail" | "warn" | "info">("");
  const [category, setCategory] = useState("");
  const [r2oStatus, setR2oStatus] = useState<"" | "issues" | R2OStatus>("issues");
  const [r2oStage, setR2oStage] = useState("");
  const [clientFilter, setClientFilter] = useState<"" | "poor" | "issues">("issues");
  const [apFilter, setApFilter] = useState<"" | "issues">("");
  const [showAll, setShowAll] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [coverage, setCoverage] = useState<CommandCoverage[]>([]);
  const [r2oCoverage, setR2oCoverage] = useState<CommandCoverage[]>([]);
  const [adding, setAdding] = useState<string | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceTypes, setDeviceTypes] = useState<DeviceType[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [collectTarget, setCollectTarget] = useState<Device[] | null>(null);

  async function run(dns = withDns) {
    setRunning(true);
    setError(null);
    try {
      const { data } = await wirelessAuditApi.run(dns);
      setReport(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setRunning(false);
    }
  }

  async function loadCoverage() {
    try {
      const [a, b] = await Promise.all([wirelessAuditApi.coverage(), wirelessAuditApi.r2oCoverage()]);
      setCoverage(a.data);
      setR2oCoverage(b.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useEffect(() => {
    void run();
    void loadCoverage();
    Promise.all([devicesApi.list(), deviceTypesApi.list(), credentialsApi.list()])
      .then(([d, t, c]) => {
        setDevices(d.data);
        setDeviceTypes(t.data);
        setCredentials(c.data);
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const controllers = useMemo(() => devices.filter((d) => WLC_TYPES.has(d.device_type)), [devices]);

  async function handleDownload() {
    setDownloading(true);
    try {
      const response = await wirelessAuditApi.exportXlsx(withDns);
      saveBlobResponse(response, `wireless-audit-${formatLocalDateTime(new Date()).replace(/[: ]/g, "-")}.xlsx`);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setDownloading(false);
    }
  }

  async function handleAdd(deviceType: string, r2o: boolean) {
    setAdding(`${r2o ? "r2o" : "audit"}:${deviceType}`);
    setNotice(null);
    try {
      const { data } = r2o ? await wirelessAuditApi.addR2OCommands(deviceType) : await wirelessAuditApi.addCommands(deviceType);
      setNotice(`${data.label} now collects ${data.commands.length} commands. Collect the controllers again, then re-run the audit.`);
      await loadCoverage();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setAdding(null);
    }
  }

  function toggle(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const q = query.trim();
  const findings = useMemo(
    () =>
      (report?.findings ?? []).filter(
        (f) =>
          (!controller || f.controller_id === controller) &&
          (!severity || f.severity === severity) &&
          (!category || f.category === category) &&
          matches(q, f.controller_name, f.subject, f.message, f.expected, f.actual, f.category),
      ),
    [report, controller, severity, category, q],
  );
  const categories = useMemo(() => Array.from(new Set((report?.findings ?? []).map((f) => f.category))).sort(), [report]);
  const r2oRows = useMemo(
    () =>
      (report?.r2o ?? []).filter(
        (r) =>
          (!controller || r.controller_id === controller) &&
          (!r2oStage || r.stage === r2oStage) &&
          (!r2oStatus || (r2oStatus === "issues" ? r.status === "fail" || r.status === "warn" || r.status === "no_data" : r.status === r2oStatus)) &&
          matches(q, r.controller_name, r.description, r.commands.join(" "), r.details.join(" ")),
      ),
    [report, controller, r2oStatus, r2oStage, q],
  );
  const apRows = useMemo(
    () =>
      (report?.access_points ?? []).filter(
        (a) =>
          (!controller || a.controller_id === controller) &&
          (!apFilter || a.issues > 0) &&
          matches(q, a.controller_name, a.name, a.model, a.ip, a.mac, a.serial, a.ap_group, a.site_tag, a.policy_tag, a.fqdn, a.ptr),
      ),
    [report, controller, apFilter, q],
  );
  const clientRows = useMemo(
    () =>
      (report?.clients ?? []).filter(
        (c) =>
          (!controller || c.controller_id === controller) &&
          (!clientFilter || (clientFilter === "poor" ? c.health === "poor" : c.issues.length > 0)) &&
          matches(q, c.controller_name, c.mac, c.ip, c.ap_name, c.ssid, c.username, c.manufacturer, c.state),
      ),
    [report, controller, clientFilter, q],
  );
  const groupRows = useMemo(
    () => (report?.groups ?? []).filter((g) => (!controller || g.controller_id === controller) && matches(q, g.controller_name, g.name, g.kind, g.models, g.wlans, g.profiles)),
    [report, controller, q],
  );
  const missingCoverage = coverage.filter((c) => c.device_count > 0 && c.missing.length > 0).length + r2oCoverage.filter((c) => c.device_count > 0 && c.missing.length > 0).length;

  const s = report?.summary;
  const r2oIssues = (report?.r2o ?? []).filter((r) => r.status === "fail").length;
  const limit = (n: number) => (showAll ? n : Math.min(n, ROW_LIMIT));

  return (
    <div className="page page-wide">
      <div className="page-header-row">
        <div>
          <h1>Wireless Audit</h1>
          <p className="page-subtitle" style={{ marginTop: 4 }}>
            Checks every Catalyst 9800 and AireOS controller against your wireless standard and Return-to-Operations checklist, from
            the latest collected snapshot of each: configuration against the golden template, AP naming, addressing and DNS, AP groups and
            tags, WNCD balance, radios, client health and the controller logs. Collect the controllers first so the snapshots are fresh;
            import the standard and R2O workbooks under <em>Standard</em>.
            {report ? ` Generated ${new Date(report.generated_at).toLocaleString()}${report.dns_checked ? " with live DNS lookups" : " without DNS"}.` : ""}
          </p>
        </div>
        <div className="page-actions">
          <label className="inline-check" title="Look up every AP name and address in DNS (forward and PTR)">
            <input type="checkbox" checked={withDns} onChange={(e) => setWithDns(e.target.checked)} /> DNS lookups
          </label>
          <button className="link-button" onClick={() => setCollectTarget(controllers)} disabled={controllers.length === 0} title="Log in to every controller and collect a fresh snapshot">
            Collect controllers ({controllers.length})
          </button>
          <button className="link-button" onClick={() => void run()} disabled={running}>
            {running ? "Running…" : "Run audit"}
          </button>
          <button onClick={handleDownload} disabled={downloading || !report}>
            {downloading ? "Preparing…" : "Download Excel"}
          </button>
        </div>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="info-banner">{notice}</div>}

      {!report && !error && <p>Running the audit…</p>}

      {report && s && (
        <>
          <div className="monitor-tiles" role="group" aria-label="Audit summary">
            <StatTile label="Controllers" value={`${s.controllers_collected}/${s.controllers}`} hint="Controllers with a collected snapshot, of all 9800/AireOS devices" tone={s.controllers_collected < s.controllers ? "unknown" : "up"} />
            <StatTile label="Failures" value={String(s.fail)} tone={s.fail ? "down" : "up"} />
            <StatTile label="Warnings" value={String(s.warn)} tone={s.warn ? "unknown" : "up"} />
            <StatTile label="R2O failures" value={String(r2oIssues)} tone={r2oIssues ? "down" : "up"} hint="Return-to-Operations checks failing" />
            <StatTile label="APs with issues" value={`${s.aps_with_issues}/${s.access_points}`} tone={s.aps_with_issues ? "unknown" : "up"} />
            <StatTile label="Poor clients" value={`${s.clients_poor}/${s.clients}`} tone={s.clients_poor ? "unknown" : "up"} hint="Clients below the RSSI/SNR failure thresholds" />
          </div>

          {report.controllers.length === 0 && (
            <div className="info-banner">
              No wireless controllers yet. Add your 9800 / AireOS controllers on the <Link to="/devices">Devices</Link> page (type "Cisco
              Catalyst 9800 WLC" or "Cisco AireOS WLC").
            </div>
          )}

          <div className="page-header-row" style={{ marginTop: 10 }}>
            <div className="terminal-tabs" role="tablist" style={{ margin: 0 }}>
              {TABS.map((t) => (
                <button key={t.key} role="tab" aria-selected={tab === t.key} className={`terminal-tab ${tab === t.key ? "active" : ""}`} onClick={() => setTab(t.key)}>
                  <span className="terminal-tab-name">
                    {t.label}
                    {t.key === "findings" ? ` (${s.fail + s.warn})` : ""}
                    {t.key === "r2o" && r2oIssues ? ` (${r2oIssues})` : ""}
                    {t.key === "coverage" && missingCoverage ? ` (${missingCoverage})` : ""}
                  </span>
                </button>
              ))}
            </div>
            {!["standard", "coverage", "overview"].includes(tab) && (
              <div className="audit-filters">
                <select value={controller} onChange={(e) => setController(e.target.value)} aria-label="Controller">
                  <option value="">All controllers</option>
                  {report.controllers.map((c) => (
                    <option key={c.device_id} value={c.device_id}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search…" style={{ width: 220 }} />
              </div>
            )}
          </div>

          {tab === "overview" && <Overview controllers={report.controllers} expanded={expanded} toggle={toggle} />}

          {tab === "r2o" && (
            <>
              <div className="audit-filters" style={{ margin: "10px 0" }}>
                <select value={r2oStatus} onChange={(e) => setR2oStatus(e.target.value as typeof r2oStatus)} aria-label="Status">
                  <option value="issues">Needs attention</option>
                  <option value="">All checks</option>
                  <option value="fail">Fail</option>
                  <option value="warn">Warning</option>
                  <option value="manual">Manual review</option>
                  <option value="no_data">No data</option>
                  <option value="pass">Pass</option>
                  <option value="na">Not applicable</option>
                </select>
                <select value={r2oStage} onChange={(e) => setR2oStage(e.target.value)} aria-label="Stage">
                  <option value="">PRE and POST cutover</option>
                  <option value="PRE">PRE cutover</option>
                  <option value="POST">POST cutover</option>
                </select>
                <span className="table-hint">{r2oRows.length} check(s)</span>
              </div>
              {(report.r2o ?? []).length === 0 ? (
                <div className="info-banner">No Return-to-Operations checklist yet: import the R2O workbook under the Standard tab, add its commands on the Coverage tab, then collect the controllers.</div>
              ) : (
                <R2OTable rows={r2oRows.slice(0, limit(r2oRows.length))} expanded={expanded} toggle={toggle} />
              )}
            </>
          )}

          {tab === "findings" && (
            <>
              <div className="audit-filters" style={{ margin: "10px 0" }}>
                <select value={severity} onChange={(e) => setSeverity(e.target.value as typeof severity)} aria-label="Severity">
                  <option value="">All severities</option>
                  <option value="fail">Fail</option>
                  <option value="warn">Warning</option>
                  <option value="info">Note</option>
                </select>
                <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
                  <option value="">All categories</option>
                  {categories.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
                <span className="table-hint">{findings.length} finding(s)</span>
              </div>
              <FindingsTable rows={findings.slice(0, limit(findings.length))} />
            </>
          )}

          {tab === "aps" && (
            <>
              <div className="audit-filters" style={{ margin: "10px 0" }}>
                <select value={apFilter} onChange={(e) => setApFilter(e.target.value as typeof apFilter)} aria-label="APs">
                  <option value="">All APs</option>
                  <option value="issues">APs with issues</option>
                </select>
                <span className="table-hint">{apRows.length} AP(s)</span>
              </div>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Controller</th>
                    <th>AP</th>
                    <th>Model</th>
                    <th>IP</th>
                    <th title="Name follows the naming standard">Name</th>
                    <th title="IP inside the AP management subnets">Subnet</th>
                    <th title="Forward DNS gives the AP's IP">DNS</th>
                    <th title="PTR gives the AP's name">PTR</th>
                    <th>Group / tags</th>
                    <th>Uplink</th>
                    <th>Radios</th>
                    <th>Clients</th>
                    <th>Issues</th>
                  </tr>
                </thead>
                <tbody>
                  {apRows.slice(0, limit(apRows.length)).map((a) => (
                    <tr key={`${a.controller_id}|${a.name}`}>
                      <td>{a.controller_name}</td>
                      <td>
                        <strong>{a.name}</strong>
                        {a.state && a.state !== "Registered" ? <span className="audit-bad"> {a.state}</span> : null}
                      </td>
                      <td>{dash(a.model)}</td>
                      <td>{dash(a.ip)}</td>
                      <td>
                        <YesNo value={a.name_ok} />
                      </td>
                      <td>
                        <YesNo value={a.subnet_ok} />
                      </td>
                      <td>
                        <YesNo value={a.dns_ok} title={a.fqdn ? `${a.fqdn} → ${a.dns_ips.join(", ") || "no record"}` : undefined} />
                      </td>
                      <td>
                        <YesNo value={a.ptr_ok} title={a.ptr ?? "no PTR"} />
                      </td>
                      <td>
                        {a.ap_group ?? ([a.site_tag, a.policy_tag, a.rf_tag].filter(Boolean).join(" / ") || "—")}
                      </td>
                      <td>{dash(a.uplink)}</td>
                      <td className="table-hint">{dash(a.radios)}</td>
                      <td>{dash(a.clients)}</td>
                      <td>{a.issues ? <span className="audit-bad">{a.issues}</span> : <span className="audit-ok">0</span>}</td>
                    </tr>
                  ))}
                  {apRows.length === 0 && (
                    <tr>
                      <td colSpan={13} className="empty-state">
                        No access points.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </>
          )}

          {tab === "groups" && (
            <table className="data-table" style={{ marginTop: 10 }}>
              <thead>
                <tr>
                  <th>Controller</th>
                  <th>Kind</th>
                  <th>Name</th>
                  <th>APs</th>
                  <th>Models</th>
                  <th>WLANs</th>
                  <th>Profiles</th>
                  <th>WNCD</th>
                  <th>Issues</th>
                </tr>
              </thead>
              <tbody>
                {groupRows.map((g) => (
                  <tr key={`${g.controller_id}|${g.kind}|${g.name}`}>
                    <td>{g.controller_name}</td>
                    <td>{g.kind}</td>
                    <td>
                      <strong>{g.name}</strong>
                      {g.description ? <div className="table-hint">{g.description}</div> : null}
                    </td>
                    <td>{g.ap_count}</td>
                    <td className="table-hint">{dash(g.models)}</td>
                    <td className="table-hint">{dash(g.wlans)}</td>
                    <td className="table-hint">{dash(g.profiles)}</td>
                    <td>{dash(g.wncd)}</td>
                    <td>{g.issues ? <span className="audit-bad">{g.issues}</span> : <span className="audit-ok">0</span>}</td>
                  </tr>
                ))}
                {groupRows.length === 0 && (
                  <tr>
                    <td colSpan={9} className="empty-state">
                      No AP groups or tags collected ('show wlan apgroups' on AireOS, 'show ap tag summary' and the running config on a 9800).
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}

          {tab === "wncd" && (
            <table className="data-table" style={{ marginTop: 10 }}>
              <thead>
                <tr>
                  <th>Controller</th>
                  <th>WNCD instance</th>
                  <th>APs</th>
                  <th>CPU (5 min)</th>
                  <th>Site tags</th>
                </tr>
              </thead>
              <tbody>
                {(report.wncd ?? [])
                  .filter((w) => !controller || w.controller_id === controller)
                  .map((w) => (
                    <tr key={`${w.controller_id}|${w.instance}`}>
                      <td>{w.controller_name}</td>
                      <td>wncd_{w.instance}</td>
                      <td>{w.aps}</td>
                      <td>{w.cpu_5min === null ? "—" : `${w.cpu_5min}%`}</td>
                      <td className="table-hint">{w.site_tags.join(", ") || "—"}</td>
                    </tr>
                  ))}
                {(report.wncd ?? []).length === 0 && (
                  <tr>
                    <td colSpan={5} className="empty-state">
                      No WNCD data: 9800 only, from 'show wireless loadbalance tag affinity' and 'show processes cpu platform sorted | i wncd'.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}

          {tab === "clients" && (
            <>
              <table className="data-table" style={{ marginTop: 10 }}>
                <thead>
                  <tr>
                    <th>Controller</th>
                    <th>Clients</th>
                    <th>Good / fair / poor</th>
                    <th>Avg RSSI</th>
                    <th>Avg SNR</th>
                    <th>2.4 / 5 / 6 GHz</th>
                    <th>Not in RUN</th>
                    <th>No IP</th>
                    <th>Legacy</th>
                  </tr>
                </thead>
                <tbody>
                  {report.client_health.map((h) => (
                    <tr key={h.controller_id}>
                      <td>{h.controller_name}</td>
                      <td>
                        {h.total}
                        {h.total > h.with_rf_detail ? <span className="table-hint"> ({h.with_rf_detail} with RF detail)</span> : null}
                      </td>
                      <td>
                        <span className="audit-ok">{h.good}</span> / <span className="audit-warn">{h.fair}</span> / <span className="audit-bad">{h.poor}</span>
                      </td>
                      <td>{h.avg_rssi === null ? "—" : `${h.avg_rssi} dBm`}</td>
                      <td>{h.avg_snr === null ? "—" : `${h.avg_snr} dB`}</td>
                      <td>
                        {h.band_24} / {h.band_5} / {h.band_6}
                      </td>
                      <td>{h.not_run}</td>
                      <td>{h.no_ip}</td>
                      <td>{h.legacy}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="audit-filters" style={{ margin: "14px 0 8px" }}>
                <select value={clientFilter} onChange={(e) => setClientFilter(e.target.value as typeof clientFilter)} aria-label="Clients">
                  <option value="issues">Clients with issues</option>
                  <option value="poor">Poor signal only</option>
                  <option value="">All clients</option>
                </select>
                <span className="table-hint">{clientRows.length} client(s)</span>
              </div>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Controller</th>
                    <th>MAC</th>
                    <th>User / IP</th>
                    <th>AP</th>
                    <th>SSID</th>
                    <th>State</th>
                    <th>Protocol</th>
                    <th>RSSI</th>
                    <th>SNR</th>
                    <th>Health</th>
                    <th>Issues</th>
                  </tr>
                </thead>
                <tbody>
                  {clientRows.slice(0, limit(clientRows.length)).map((c) => (
                    <tr key={`${c.controller_id}|${c.mac}`}>
                      <td>{c.controller_name}</td>
                      <td>
                        {c.mac}
                        {c.manufacturer ? <div className="table-hint">{c.manufacturer}</div> : null}
                      </td>
                      <td>
                        {dash(c.username)}
                        <div className="table-hint">{dash(c.ip)}</div>
                      </td>
                      <td>{dash(c.ap_name)}</td>
                      <td>{dash(c.ssid)}</td>
                      <td>{dash(c.state)}</td>
                      <td>{dash(c.protocol)}</td>
                      <td>{c.rssi === null ? "—" : `${c.rssi} dBm`}</td>
                      <td>{c.snr === null ? "—" : `${c.snr} dB`}</td>
                      <td>
                        <span className={`audit-health audit-health-${c.health}`}>{c.health}</span>
                      </td>
                      <td className="table-hint">{c.issues.join(", ") || "—"}</td>
                    </tr>
                  ))}
                  {clientRows.length === 0 && (
                    <tr>
                      <td colSpan={11} className="empty-state">
                        No clients match.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </>
          )}

          {tab === "logs" && (
            <table className="data-table" style={{ marginTop: 10 }}>
              <thead>
                <tr>
                  <th>Controller</th>
                  <th>Source</th>
                  <th>Message</th>
                  <th>Severity</th>
                  <th>Count</th>
                  <th>Latest</th>
                </tr>
              </thead>
              <tbody>
                {report.logs
                  .filter((g) => (!controller || g.controller_id === controller) && matches(q, g.key, g.example, g.controller_name))
                  .map((g) => (
                    <tr key={`${g.controller_id}|${g.source}|${g.key}`}>
                      <td>{g.controller_name}</td>
                      <td>{g.source}</td>
                      <td>
                        <strong className={g.notable || (g.severity !== null && g.severity <= 3) ? "audit-bad" : undefined}>{g.key}</strong>
                      </td>
                      <td>{dash(g.severity)}</td>
                      <td>{g.count}</td>
                      <td className="audit-mono">{g.example}</td>
                    </tr>
                  ))}
                {report.logs.length === 0 && (
                  <tr>
                    <td colSpan={6} className="empty-state">
                      No log output collected ('show logging' on a 9800, 'show msglog' / 'show traplog' on AireOS).
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}

          {["r2o", "findings", "aps", "clients"].includes(tab) && !showAll && (
            <p className="table-hint">
              Tables show the first {ROW_LIMIT} rows.{" "}
              <button className="link-button" style={{ padding: 0 }} onClick={() => setShowAll(true)}>
                Show all
              </button>{" "}
              (the Excel download always has every row)
            </p>
          )}

          {tab === "coverage" && (
            <CoverageTab coverage={coverage} r2oCoverage={r2oCoverage} isAdmin={isAdmin} adding={adding} onAdd={handleAdd} />
          )}
        </>
      )}

      {tab === "standard" && (
        <WirelessStandardEditor
          isAdmin={isAdmin}
          controllers={controllers}
          onSaved={() => {
            void run();
            void loadCoverage();
          }}
        />
      )}

      {collectTarget && (
        <StartCollectionModal
          devices={collectTarget}
          deviceTypes={deviceTypes}
          credentials={credentials}
          onClose={() => setCollectTarget(null)}
          onStarted={(job) => {
            setCollectTarget(null);
            navigate(`/jobs/${job.id}`);
          }}
        />
      )}
    </div>
  );
}

function Overview({ controllers, expanded, toggle }: { controllers: ControllerAudit[]; expanded: Set<string>; toggle: (key: string) => void }) {
  return (
    <table className="data-table" style={{ marginTop: 10 }}>
      <thead>
        <tr>
          <th>Controller</th>
          <th>Platform</th>
          <th>Model / software</th>
          <th>Collected</th>
          <th>APs</th>
          <th>Clients</th>
          <th>Score</th>
          <th>Checks</th>
          <th>Return to Operations</th>
        </tr>
      </thead>
      <tbody>
        {controllers.map((c) => {
          const open = expanded.has(`ctl:${c.device_id}`);
          return (
            <Fragment key={c.device_id}>
              <tr className="audit-clickable" onClick={() => toggle(`ctl:${c.device_id}`)}>
                <td>
                  <span className="audit-caret">{open ? "▾" : "▸"}</span> <strong>{c.name}</strong>
                  <div className="table-hint">
                    {c.host}
                    {c.site ? ` · ${c.site}` : ""}
                  </div>
                </td>
                <td>{c.platform === "9800" ? "Catalyst 9800" : "AireOS"}</td>
                <td>
                  {dash(c.model)}
                  <div className="table-hint">{dash(c.software)}</div>
                </td>
                <td>{c.collected_at ? new Date(c.collected_at).toLocaleString() : <span className="audit-bad">never</span>}</td>
                <td>{c.ap_count}</td>
                <td>{c.client_count}</td>
                <td>
                  <ScoreCell score={c.score} />
                </td>
                <td>
                  {(["fail", "warn", "pass"] as const).map((k) =>
                    c.counts[k] ? (
                      <span key={k} style={{ marginRight: 6 }}>
                        <AuditBadge status={k} /> {c.counts[k]}
                      </span>
                    ) : null,
                  )}
                  {c.template_profile ? <div className="table-hint">Standard: {c.template_profile}</div> : null}
                </td>
                <td>
                  {c.r2o_checklist ? (
                    <>
                      {(["fail", "warn", "no_data", "manual", "pass"] as const).map((k) =>
                        c.r2o_counts[k] ? (
                          <span key={k} style={{ marginRight: 6 }}>
                            <AuditBadge status={k} /> {c.r2o_counts[k]}
                          </span>
                        ) : null,
                      )}
                      <div className="table-hint">{c.r2o_checklist}</div>
                    </>
                  ) : (
                    <span className="table-hint">no checklist</span>
                  )}
                </td>
              </tr>
              {open && (
                <tr>
                  <td colSpan={9} className="audit-detail">
                    <table className="data-table audit-subtable">
                      <thead>
                        <tr>
                          <th>Category</th>
                          <th>Check</th>
                          <th>Status</th>
                          <th>Evaluated</th>
                          <th>Issues</th>
                          <th>Note</th>
                        </tr>
                      </thead>
                      <tbody>
                        {c.checks.map((ch) => (
                          <tr key={ch.check_id}>
                            <td>{ch.category}</td>
                            <td>{ch.title}</td>
                            <td>
                              <AuditBadge status={ch.status} />
                            </td>
                            <td>{ch.evaluated}</td>
                            <td>{ch.issues}</td>
                            <td className="table-hint">{ch.note ?? ""}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {c.missing_commands.length > 0 && (
                      <p className="table-hint">Not collected (add on the Coverage tab): {c.missing_commands.join(", ")}</p>
                    )}
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function R2OTable({ rows, expanded, toggle }: { rows: R2OResult[]; expanded: Set<string>; toggle: (key: string) => void }) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Status</th>
          <th>Controller</th>
          <th>Check</th>
          <th>Stage</th>
          <th>Result</th>
          <th>How to fix</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const key = `r2o:${r.controller_id}:${r.item_id}`;
          const open = expanded.has(key);
          return (
            <Fragment key={key}>
              <tr>
                <td>
                  <AuditBadge status={r.status} />
                  {r.level ? <div className="table-hint">{r.level === "M" ? "Mandatory" : r.level === "G" ? "Gold" : r.level}</div> : null}
                </td>
                <td>{r.controller_name}</td>
                <td>
                  <strong>{r.description}</strong>
                  <div className="table-hint audit-mono">{r.commands.slice(0, 4).join(" · ")}{r.commands.length > 4 ? ` · +${r.commands.length - 4}` : ""}</div>
                  <div className="table-hint">
                    {r.checklist} row {r.row}
                    {r.output ? (
                      <>
                        {" · "}
                        <button className="link-button" style={{ padding: 0, fontSize: "inherit" }} onClick={() => toggle(key)}>
                          {open ? "hide output" : "show output"}
                        </button>
                      </>
                    ) : null}
                  </div>
                </td>
                <td>{dash(r.stage)}</td>
                <td>
                  <ul className="audit-list">
                    {r.details.slice(0, 12).map((d, i) => (
                      <li key={i}>{d}</li>
                    ))}
                    {r.details.length > 12 ? <li>… {r.details.length - 12} more</li> : null}
                  </ul>
                </td>
                <td>{r.fix.length ? <pre className="audit-fix">{r.fix.join("\n")}</pre> : <span className="table-hint">—</span>}</td>
              </tr>
              {open && (
                <tr>
                  <td colSpan={6} className="audit-detail">
                    <pre className="audit-output">{r.output}</pre>
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
        {rows.length === 0 && (
          <tr>
            <td colSpan={6} className="empty-state">
              Nothing to show for this filter.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

function FindingsTable({ rows }: { rows: AuditFinding[] }) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Severity</th>
          <th>Controller</th>
          <th>Category</th>
          <th>Subject</th>
          <th>Finding</th>
          <th>Expected</th>
          <th>Actual</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((f, i) => (
          <tr key={i}>
            <td>
              <AuditBadge status={f.severity} />
            </td>
            <td>{f.controller_name}</td>
            <td>{f.category}</td>
            <td>{dash(f.subject)}</td>
            <td>{f.message}</td>
            <td className="table-hint audit-mono">{dash(f.expected)}</td>
            <td className="table-hint audit-mono">{dash(f.actual)}</td>
          </tr>
        ))}
        {rows.length === 0 && (
          <tr>
            <td colSpan={7} className="empty-state">
              No findings for this filter.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

function CoverageTab({
  coverage,
  r2oCoverage,
  isAdmin,
  adding,
  onAdd,
}: {
  coverage: CommandCoverage[];
  r2oCoverage: CommandCoverage[];
  isAdmin: boolean;
  adding: string | null;
  onAdd: (deviceType: string, r2o: boolean) => void;
}) {
  const section = (title: string, rows: CommandCoverage[], r2o: boolean) => (
    <>
      <h2 style={{ marginTop: 18 }}>{title}</h2>
      {rows.length === 0 ? (
        <p className="table-hint">{r2o ? "Import the R2O checklist under Standard first." : "No controller types."}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Device type</th>
              <th>Devices</th>
              <th>Missing commands</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.device_type}>
                <td>
                  {c.label}
                  {c.is_custom_profile ? <div className="table-hint">custom command list</div> : null}
                </td>
                <td>{c.device_count}</td>
                <td className="audit-mono">{c.missing.length ? c.missing.join(", ") : <span className="audit-ok">everything collected</span>}</td>
                <td>
                  {c.missing.length > 0 && isAdmin && (
                    <button disabled={adding === `${r2o ? "r2o" : "audit"}:${c.device_type}`} onClick={() => onAdd(c.device_type, r2o)}>
                      {adding === `${r2o ? "r2o" : "audit"}:${c.device_type}` ? "Adding…" : `Add ${c.missing.length}`}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
  return (
    <div>
      <p className="page-subtitle" style={{ marginTop: 10 }}>
        The audit and the Return-to-Operations checks read the output of specific commands. Adding them changes this organization's
        command list for that controller type (Commands page); they are collected on the next collection. "{"{site}"}" runs with the first 8
        characters of the device name, "{"{ap}"}" once for every AP.
      </p>
      {section("Wireless Audit commands", coverage, false)}
      {section("Return to Operations commands", r2oCoverage, true)}
    </div>
  );
}
