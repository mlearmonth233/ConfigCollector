import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { inventoryApi } from "../api/resources";
import type { Inventory as InventoryData, InventoryCommandCoverage } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { formatLocalDateTime } from "../utils/formatDate";
import { useLicence } from "../context/LicenceContext";
import { UpgradeNotice } from "../components/UpgradeNotice";

type Tab = "devices" | "hardware" | "neighbors" | "aps" | "subnets" | "unmanaged" | "endpoints" | "coverage";

const TABS: { key: Tab; label: string }[] = [
  { key: "devices", label: "Devices" },
  { key: "hardware", label: "Hardware" },
  { key: "neighbors", label: "Neighbors" },
  { key: "aps", label: "Access points" },
  { key: "subnets", label: "Subnets" },
  { key: "unmanaged", label: "Unmanaged" },
  { key: "endpoints", label: "Endpoints" },
  { key: "coverage", label: "Coverage" },
];

/** "10.10.10.1 on HQ-CORE-SW01 Vlan10" style summary of one gateway/interface row. */
function describeAddress(a: { device_name: string; interface: string; ip: string; secondary: boolean }): string {
  return `${a.device_name} ${a.interface} ${a.ip}${a.secondary ? " (secondary)" : ""}`;
}

function UsageBar({ used, capacity }: { used: number; capacity: number }) {
  if (!capacity) return <span className="field-hint">—</span>;
  const percent = Math.min(100, Math.round((100 * used) / capacity));
  return (
    <div className="usage-bar" title={`${used} of ${capacity} usable addresses seen`}>
      <div className="usage-bar-track">
        <div className={`usage-bar-fill ${percent >= 90 ? "high" : percent >= 70 ? "mid" : ""}`} style={{ width: `${percent}%` }} />
      </div>
      <span className="usage-bar-label">
        {used}/{capacity}
      </span>
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

function matches(query: string, ...fields: (string | null | undefined)[]): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return fields.some((f) => (f ?? "").toLowerCase().includes(q));
}

function dash(value: string | null | undefined): string {
  return value && value.trim() ? value : "—";
}

/** Everything the collected configs say about the hardware on site: the
 * managed devices' models and serials, their line cards and optics, what
 * CDP/LLDP see plugged into each port, access points, and the MAC/IP of
 * every endpoint on an access port. Downloadable as one Excel workbook. */
export function Inventory() {
  const { hasFeature } = useLicence();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [data, setData] = useState<InventoryData | null>(null);
  const [coverage, setCoverage] = useState<InventoryCommandCoverage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("devices");
  const [query, setQuery] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    setRefreshing(true);
    try {
      const [inv, cov] = await Promise.all([inventoryApi.get(), inventoryApi.coverage()]);
      setData(inv.data);
      setCoverage(cov.data);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function handleDownload() {
    setDownloading(true);
    setError(null);
    try {
      const response = await inventoryApi.exportXlsx();
      const url = URL.createObjectURL(response.data);
      const a = document.createElement("a");
      a.href = url;
      const disposition = String(response.headers?.["content-disposition"] ?? "");
      const match = /filename="([^"]+)"/.exec(disposition);
      a.download = match?.[1] ?? `${(user?.org_name ?? "packrat").replace(/[^A-Za-z0-9._-]+/g, "-")}-inventory-${formatLocalDateTime(new Date()).replace(/[: ]/g, "-")}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setDownloading(false);
    }
  }

  async function handleAddCommands(deviceType: string) {
    setAdding(deviceType);
    setNotice(null);
    try {
      const { data: profile } = await inventoryApi.addCommands(deviceType);
      setNotice(`${profile.label} now collects ${profile.commands.length} commands. The inventory fills in after the next collection of those devices.`);
      setCoverage((await inventoryApi.coverage()).data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setAdding(null);
    }
  }

  const missingCoverage = useMemo(() => coverage.filter((c) => c.device_count > 0 && c.missing.length > 0), [coverage]);
  const notCollected = useMemo(() => data?.devices.filter((d) => !d.has_snapshot) ?? [], [data]);
  const topModels = useMemo(() => Object.entries(data?.models ?? {}).slice(0, 8), [data]);

  if (error && !data) return <div className="page error-banner">{error}</div>;
  if (!data) return <div className="page">Loading…</div>;

  const s = data.summary;
  const q = query.trim();

  return (
    <div className="page page-wide">
      <div className="page-header-row">
        <div>
          <h1>Inventory</h1>
          <p className="page-subtitle" style={{ marginTop: 4 }}>
            Built from the latest collected config of every device: model, serial and software from "show version" and
            "show inventory", what CDP and LLDP see on each port, access points on your controllers, and the MAC and IP
            addresses sitting behind access ports. Generated {new Date(data.generated_at).toLocaleString()}; it refreshes
            itself from the newest snapshots every time you open it.
          </p>
        </div>
        <div className="page-actions">
          <button className="link-button" onClick={load} disabled={refreshing}>
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
          <button onClick={handleDownload} disabled={downloading || !hasFeature("inventory_export")} title={hasFeature("inventory_export") ? undefined : "The Excel export is part of Colony and Warren"}>
            {downloading ? "Preparing…" : "Download Excel"}
          </button>
        </div>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="info-banner">{notice}</div>}
      <UpgradeNotice feature="inventory_export">The inventory itself is free to browse here; downloading it as an Excel workbook needs a licence key.</UpgradeNotice>

      <div className="monitor-tiles" role="group" aria-label="Inventory summary">
        <StatTile label="Devices" value={`${s.devices_with_config}/${s.devices}`} hint="Devices with a collected config, of all managed devices" tone={s.devices_with_config < s.devices ? "unknown" : "up"} />
        <StatTile label="Serials found" value={String(s.devices_with_serial)} hint="Devices whose serial number was read from show version / show inventory" />
        <StatTile label="Components" value={String(s.hardware)} hint="Chassis, modules, power supplies and optics from show inventory" />
        <StatTile label="Access points" value={String(s.access_points)} />
        <StatTile label="Subnets" value={String(s.subnets)} hint="IP subnets in use: interface addresses in the collected configs, plus ranges only seen in ARP tables" />
        <StatTile label="Unmanaged" value={String(s.unmanaged)} tone={s.unmanaged ? "unknown" : "neutral"} hint="Devices CDP/LLDP can see that are not in your Devices list" />
      </div>

      {(missingCoverage.length > 0 || notCollected.length > 0) && (
        <div className="info-banner" style={{ marginBottom: 14 }}>
          {notCollected.length > 0 && (
            <div>
              {notCollected.length} device{notCollected.length === 1 ? " has" : "s have"} never been collected, so{" "}
              {notCollected.length === 1 ? "it has" : "they have"} no inventory yet: run a collection from <Link to="/devices">Devices</Link>.
            </div>
          )}
          {missingCoverage.length > 0 && (
            <div>
              {missingCoverage.map((c) => c.label).join(", ")}: the command list does not collect everything the inventory reads. See the{" "}
              <button type="button" className="link-button" style={{ padding: 0, fontSize: "inherit" }} onClick={() => setTab("coverage")}>
                Coverage
              </button>{" "}
              tab to add the missing commands in one click.
            </div>
          )}
        </div>
      )}

      {topModels.length > 0 && (
        <p className="page-subtitle" style={{ marginTop: 0 }}>
          <strong>Models on site:</strong>{" "}
          {topModels.map(([model, count]) => `${model} ×${count}`).join(" · ")}
          {Object.keys(data.models).length > topModels.length ? ` · +${Object.keys(data.models).length - topModels.length} more in the Excel summary` : ""}
        </p>
      )}

      <div className="page-header-row" style={{ marginTop: 10 }}>
        <div className="terminal-tabs" role="tablist" style={{ margin: 0 }}>
          {TABS.map((t) => (
            <button key={t.key} role="tab" aria-selected={tab === t.key} className={`terminal-tab ${tab === t.key ? "active" : ""}`} onClick={() => setTab(t.key)}>
              <span className="terminal-tab-name">
                {t.label}
                {t.key === "coverage" && missingCoverage.length > 0 ? ` (${missingCoverage.length})` : ""}
                {t.key === "unmanaged" && s.unmanaged > 0 ? ` (${s.unmanaged})` : ""}
              </span>
            </button>
          ))}
        </div>
        {tab !== "coverage" && (
          <input id="inventory-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search name, model, serial, MAC, IP…" style={{ width: 280, maxWidth: "100%" }} />
        )}
      </div>

      {tab === "devices" && (
        <table className="data-table" style={{ marginTop: 8 }}>
          <thead>
            <tr>
              <th>Device</th>
              <th>Hostname</th>
              <th>IP</th>
              <th>Model</th>
              <th>Serial</th>
              <th>Software</th>
              <th>Uptime</th>
              <th>Base MAC</th>
              <th>Site</th>
              <th>Config collected</th>
            </tr>
          </thead>
          <tbody>
            {data.devices
              .filter((d) => matches(q, d.name, d.hostname, d.host, d.model, d.serial, d.software, d.site, d.base_mac))
              .map((d) => (
                <tr key={d.device_id}>
                  <td>{d.name}</td>
                  <td>{dash(d.hostname)}</td>
                  <td>{d.host}</td>
                  <td>{dash(d.model)}</td>
                  <td style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>{dash(d.serial)}</td>
                  <td>{dash(d.software)}</td>
                  <td>{dash(d.uptime)}</td>
                  <td style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>{dash(d.base_mac)}</td>
                  <td>{dash(d.site)}</td>
                  <td>
                    {d.has_snapshot ? (
                      <>
                        {d.collected_at ? new Date(d.collected_at).toLocaleString() : "yes"}
                        {d.commands_missing.length > 0 && (
                          <span className="field-hint" style={{ display: "block" }} title={`Not in this snapshot: ${d.commands_missing.join(", ")}`}>
                            missing {d.commands_missing.length} command{d.commands_missing.length === 1 ? "" : "s"}
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="field-hint">never</span>
                    )}
                  </td>
                </tr>
              ))}
            {data.devices.length === 0 && (
              <tr>
                <td colSpan={10} className="empty-state">
                  No devices yet. <Link to="/devices">Add devices</Link>, collect their configs, and the inventory builds itself.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {tab === "hardware" && (
        <table className="data-table" style={{ marginTop: 8 }}>
          <thead>
            <tr>
              <th>Device</th>
              <th>Component</th>
              <th>Description</th>
              <th>PID</th>
              <th>VID</th>
              <th>Serial</th>
            </tr>
          </thead>
          <tbody>
            {data.hardware
              .filter((h) => matches(q, h.device_name, h.name, h.description, h.pid, h.serial))
              .map((h, i) => (
                <tr key={`${h.device_id}-${i}`}>
                  <td>{h.device_name}</td>
                  <td>{h.name}</td>
                  <td>{dash(h.description)}</td>
                  <td>{dash(h.pid)}</td>
                  <td>{dash(h.vid)}</td>
                  <td style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>{dash(h.serial)}</td>
                </tr>
              ))}
            {data.hardware.length === 0 && (
              <tr>
                <td colSpan={6} className="empty-state">
                  Nothing from "show inventory" yet. Chassis, stack members, power supplies, modules and optics appear here once a collection includes it.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {tab === "neighbors" && (
        <table className="data-table" style={{ marginTop: 8 }}>
          <thead>
            <tr>
              <th>Device</th>
              <th>Local port</th>
              <th>Neighbor</th>
              <th>Neighbor IP</th>
              <th>Platform</th>
              <th>Capabilities</th>
              <th>Remote port</th>
              <th>Via</th>
              <th>Managed</th>
            </tr>
          </thead>
          <tbody>
            {data.neighbors
              .filter((n) => matches(q, n.device_name, n.local_port, n.name, n.ip, n.platform, n.remote_port))
              .map((n, i) => (
                <tr key={`${n.device_id}-${i}`}>
                  <td>{n.device_name}</td>
                  <td>{dash(n.local_port)}</td>
                  <td>{dash(n.name)}</td>
                  <td>{dash(n.ip)}</td>
                  <td>{dash(n.platform)}</td>
                  <td>{dash(n.capabilities)}</td>
                  <td>{dash(n.remote_port)}</td>
                  <td>{n.protocol.toUpperCase()}</td>
                  <td>{n.managed_device_name ? <span className="status-badge status-completed">{n.managed_device_name}</span> : <span className="status-badge status-fallback">no</span>}</td>
                </tr>
              ))}
            {data.neighbors.length === 0 && (
              <tr>
                <td colSpan={9} className="empty-state">
                  No CDP or LLDP neighbors yet. The device type has to collect "show cdp neighbors detail" or "show lldp neighbors detail" - check the Coverage tab.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {tab === "aps" && (
        <table className="data-table" style={{ marginTop: 8 }}>
          <thead>
            <tr>
              <th>Controller</th>
              <th>AP</th>
              <th>Model</th>
              <th>MAC</th>
              <th>IP</th>
              <th>Serial</th>
              <th>Software</th>
            </tr>
          </thead>
          <tbody>
            {data.access_points
              .filter((a) => matches(q, a.controller_name, a.name, a.model, a.mac, a.ip, a.serial))
              .map((a, i) => (
                <tr key={`${a.controller_id}-${i}`}>
                  <td>{a.controller_name}</td>
                  <td>{a.name}</td>
                  <td>{dash(a.model)}</td>
                  <td style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>{dash(a.mac)}</td>
                  <td>{dash(a.ip)}</td>
                  <td style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>{dash(a.serial)}</td>
                  <td>{dash(a.software)}</td>
                </tr>
              ))}
            {data.access_points.length === 0 && (
              <tr>
                <td colSpan={7} className="empty-state">
                  No access points found. They come from a wireless controller's "show ap summary" (or "show ap config general").
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {tab === "subnets" && (
        <>
          <p className="page-subtitle" style={{ marginTop: 8 }}>
            Every IP subnet the site uses, from the interface addresses in the collected running configs (SVIs, routed ports, sub-interfaces, FortiGate and
            controller interfaces). "Addresses seen" counts the other hosts found in each range: ARP entries, access points, neighbours and your managed
            devices. A range that only appears in those, with no configured interface in any collected device, is listed as an inferred /24 so a subnet routed
            by something Packrat does not manage still shows up.
          </p>
          <table className="data-table" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>Network</th>
                <th>VLAN</th>
                <th>Name</th>
                <th>VRF</th>
                <th>Gateways / device interfaces</th>
                <th>Addresses seen</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {data.subnets
                .filter((sn) => matches(q, sn.network, sn.mask, sn.vlan, sn.name, sn.vrf, ...sn.addresses.map(describeAddress)))
                .map((sn) => (
                  <tr key={sn.network}>
                    <td style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", whiteSpace: "nowrap" }}>
                      {sn.network}
                      <div className="field-hint">{sn.mask}</div>
                    </td>
                    <td>{dash(sn.vlan)}</td>
                    <td>{dash(sn.name)}</td>
                    <td>{dash(sn.vrf)}</td>
                    <td>
                      {sn.addresses.length === 0 ? (
                        <span className="field-hint">none in a collected config</span>
                      ) : (
                        <ul className="plain-list">
                          {sn.addresses.map((a, i) => (
                            <li key={`${a.device_id}-${a.interface}-${i}`}>
                              <span style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>{a.ip}</span> {a.device_name} {a.interface}
                              {a.secondary ? <span className="field-hint"> secondary</span> : null}
                              {a.description && a.description !== sn.name ? <span className="field-hint"> · {a.description}</span> : null}
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                    <td>
                      <UsageBar used={sn.hosts_seen + sn.addresses.length} capacity={sn.usable} />
                    </td>
                    <td>{sn.source === "config" ? "interface address" : <span className="field-hint">inferred from addresses seen</span>}</td>
                  </tr>
                ))}
              {data.subnets.length === 0 && (
                <tr>
                  <td colSpan={7} className="empty-state">
                    No interface addresses found yet. The device type has to collect the running config ("show run", "show run-config" or "show
                    full-configuration"); see Coverage.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      )}

      {tab === "unmanaged" && (
        <>
          <p className="page-subtitle" style={{ marginTop: 8 }}>
            Hardware your switches can see on the wire that is not in your <Link to="/devices">Devices</Link> list. Switches and routers here are candidates to add;
            phones, access points and hosts are shown so the site map is complete.
          </p>
          <table className="data-table" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>Kind</th>
                <th>Name</th>
                <th>IP</th>
                <th>Platform</th>
                <th>Capabilities</th>
                <th>Seen from</th>
                <th>Via</th>
              </tr>
            </thead>
            <tbody>
              {data.unmanaged
                .filter((u) => matches(q, u.kind, u.name, u.ip, u.platform, ...u.seen_from))
                .map((u, i) => (
                  <tr key={`${u.name ?? u.ip}-${i}`}>
                    <td>
                      <span className={`status-badge ${u.kind === "Switch" || u.kind === "Router" ? "status-fallback" : "status-completed"}`}>{u.kind}</span>
                    </td>
                    <td>{dash(u.name)}</td>
                    <td>{dash(u.ip)}</td>
                    <td>{dash(u.platform)}</td>
                    <td>{dash(u.capabilities)}</td>
                    <td>{u.seen_from.join("; ")}</td>
                    <td>{u.protocols.map((p) => p.toUpperCase()).join("/")}</td>
                  </tr>
                ))}
              {data.unmanaged.length === 0 && (
                <tr>
                  <td colSpan={7} className="empty-state">
                    Everything CDP and LLDP report is already a managed device (or no neighbor data has been collected yet).
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      )}

      {tab === "endpoints" && (
        <>
          <p className="page-subtitle" style={{ marginTop: 8 }}>
            MAC addresses learned on each switch port ("show mac address-table"), with the IP from any device's ARP table when one is known. A port that
            leads to another switch carries that switch's whole table, so those rows are marked.
          </p>
          <table className="data-table" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>Switch</th>
                <th>Port</th>
                <th>VLAN</th>
                <th>MAC</th>
                <th>IP</th>
                <th>Type</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {data.endpoints
                .filter((e) => matches(q, e.device_name, e.port, e.vlan, e.mac, e.ip))
                .map((e, i) => (
                  <tr key={`${e.device_id}-${e.mac}-${i}`}>
                    <td>{e.device_name}</td>
                    <td>{e.port}</td>
                    <td>{dash(e.vlan)}</td>
                    <td style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>{e.mac}</td>
                    <td>{dash(e.ip)}</td>
                    <td>{dash(e.entry_type)}</td>
                    <td>{e.on_uplink ? <span className="field-hint">behind another switch</span> : ""}</td>
                  </tr>
                ))}
              {data.endpoints.length === 0 && (
                <tr>
                  <td colSpan={7} className="empty-state">
                    No MAC address tables collected yet - the device type has to run "show mac address-table" (and "show ip arp" for IPs). See Coverage.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      )}

      {tab === "coverage" && (
        <>
          <p className="page-subtitle" style={{ marginTop: 8 }}>
            The inventory only knows what the collection commands return. For each device type you use, this is what its current command list (
            <Link to="/commands">Commands</Link>) still lacks. "Add" appends the missing commands to that type's list; the next collection fills the gap.
          </p>
          <table className="data-table" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>Device type</th>
                <th>Devices</th>
                <th>Inventory commands</th>
                <th>Missing from the command list</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {coverage.map((c) => (
                <tr key={c.device_type}>
                  <td>
                    {c.label}
                    {c.is_custom_profile && <span className="field-hint" style={{ display: "block" }}>custom command list</span>}
                  </td>
                  <td>{c.device_count}</td>
                  <td style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12 }}>{c.inventory_commands.join(", ")}</td>
                  <td>
                    {c.missing.length === 0 ? (
                      <span className="status-badge status-completed">complete</span>
                    ) : (
                      <span style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12 }}>{c.missing.join(", ")}</span>
                    )}
                  </td>
                  <td>
                    {c.missing.length > 0 && isAdmin && (
                      <button className="link-button" onClick={() => handleAddCommands(c.device_type)} disabled={adding !== null}>
                        {adding === c.device_type ? "Adding…" : "Add inventory commands"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="field-hint" style={{ marginTop: 10 }}>
            Custom device types are not listed: edit their commands under Commands › Your own device types. FortiGate and APC types are read from "get system
            status" / "get system arp" and "about", which their built-in lists already include.
          </p>
        </>
      )}
    </div>
  );
}
