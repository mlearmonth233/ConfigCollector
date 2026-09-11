import { useState } from "react";

import { extractErrorMessage } from "../api/client";
import { devicesApi } from "../api/resources";
import type { DeviceImportResult, DeviceRole, DeviceType, NetworkZone } from "../api/types";

interface BulkRow {
  key: string;
  name: string;
  host: string;
  port: string;
  device_type: string;
  device_role: string;
  network_zone: "" | NetworkZone;
  site: string;
}

interface Props {
  deviceTypes: DeviceType[];
  deviceRoles: DeviceRole[];
  onClose: () => void;
  onDone: (result: DeviceImportResult) => void;
}

// Site naming convention's role code, embedded as a substring in the
// hostname (e.g. "GBGYSP01SWA001") - same codes app.services.hostname_detection
// looks for, plus RTR (routers, which that detector doesn't model yet).
// Order here is the review order the bulk-add table sorts pasted hostnames
// into, not the detector's own match order.
const ROLE_SORT_ORDER = ["RTR", "SWC", "SWD", "WLC", "SWS", "SWA", "CON", "PDU"];

function roleSortRank(name: string): number {
  const upper = name.toUpperCase();
  const rank = ROLE_SORT_ORDER.findIndex((code) => upper.includes(code));
  return rank === -1 ? ROLE_SORT_ORDER.length : rank;
}

function trailingNumber(name: string): number {
  const match = /(\d+)$/.exec(name);
  return match ? Number(match[1]) : Number.POSITIVE_INFINITY;
}

/** Groups pasted hostnames by role code in ROLE_SORT_ORDER (a name with no
 * recognized code sorts after all of them), then numerically ascending by
 * each name's trailing number within a group - e.g. RTR01, RTR02, then
 * SWC01, SWC02, ... */
function sortHostnames(names: string[]): string[] {
  return [...names].sort((a, b) => roleSortRank(a) - roleSortRank(b) || trailingNumber(a) - trailingNumber(b));
}

/** Paste a dump of hostnames -> one row per name, each auto-detected the
 * same way the single "Add device" form does (device type/role/network
 * zone from the name) -> reviewed/adjusted in an editable table -> saved
 * all at once. Host defaults to the hostname itself, since this only
 * makes sense for a network where switch/WLC/PDU names actually resolve. */
export function BulkAddDevicesModal({ deviceTypes, deviceRoles, onClose, onDone }: Props) {
  const [text, setText] = useState("");
  const [rows, setRows] = useState<BulkRow[] | null>(null);
  const [parsing, setParsing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleParse() {
    setError(null);
    const names = sortHostnames(
      Array.from(
        new Set(
          text
            .split(/[\r\n,]+/)
            .map((s) => s.trim())
            .filter(Boolean)
        )
      )
    );
    if (names.length === 0) {
      setError("Paste at least one hostname first.");
      return;
    }
    setParsing(true);
    try {
      const detections = await Promise.all(
        names.map(async (name) => {
          try {
            const { data } = await devicesApi.detect(name);
            return data;
          } catch {
            // Best-effort - a failed detection just leaves that row blank
            // for the user to fill in by hand.
            return null;
          }
        })
      );
      setRows(
        names.map((name, i) => {
          const d = detections[i];
          return {
            key: `${name}-${i}`,
            name,
            host: name,
            port: "22",
            device_type: d?.suggested_device_type ?? "",
            device_role: d?.device_role ?? "",
            network_zone: (d?.network_zone ?? "") as "" | NetworkZone,
            site: "",
          };
        })
      );
    } finally {
      setParsing(false);
    }
  }

  function updateRow(index: number, patch: Partial<BulkRow>) {
    setRows((prev) => prev && prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  function removeRow(index: number) {
    setRows((prev) => prev && prev.filter((_, i) => i !== index));
  }

  async function handleSubmit() {
    if (!rows || rows.length === 0) return;
    setError(null);
    setSubmitting(true);
    try {
      const results = await Promise.allSettled(
        rows.map((r) =>
          devicesApi.create({
            name: r.name,
            host: r.host,
            port: Number(r.port) || 22,
            device_type: r.device_type || undefined,
            device_role: r.device_role || undefined,
            network_zone: (r.network_zone || undefined) as NetworkZone | undefined,
            site: r.site || undefined,
          })
        )
      );
      const created = results.filter((r) => r.status === "fulfilled").length;
      const errors = results
        .map((r, i) => (r.status === "rejected" ? `${rows[i].name}: ${extractErrorMessage(r.reason)}` : null))
        .filter((e): e is string => e !== null);
      onDone({ created, errors });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" style={{ width: "min(1100px, 95vw)" }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Bulk add devices from hostnames</h2>
          <div className="modal-header-actions">
            <button onClick={onClose}>Cancel</button>
          </div>
        </div>
        <div className="modal-body">
          {error && <div className="error-banner">{error}</div>}

          {rows === null ? (
            <>
              <p className="page-subtitle" style={{ marginTop: 0 }}>
                Paste a list of hostnames, one per line (or comma-separated). Device type, role, and
                network zone get auto-detected from each name, same as adding one at a time - you'll
                review and can adjust everything before anything is saved.
              </p>
              <label>
                Hostnames
                <textarea
                  rows={10}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder={"GBGYSP01SWA001\nGBGYSP01SWA002\nGBGYSP01WLC001"}
                  style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
                />
              </label>
            </>
          ) : (
            <>
              <p className="page-subtitle" style={{ marginTop: 0 }}>
                Host defaults to the hostname itself (assumes it resolves on your network) - change it
                to an IP for any that don't.
              </p>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Host</th>
                    <th>Port</th>
                    <th>Type</th>
                    <th>Role</th>
                    <th>Zone</th>
                    <th>Site</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={r.key}>
                      <td>{r.name}</td>
                      <td>
                        <input value={r.host} onChange={(e) => updateRow(i, { host: e.target.value })} />
                      </td>
                      <td>
                        <input
                          type="number"
                          value={r.port}
                          onChange={(e) => updateRow(i, { port: e.target.value })}
                          style={{ width: 64 }}
                        />
                      </td>
                      <td>
                        <select value={r.device_type} onChange={(e) => updateRow(i, { device_type: e.target.value })}>
                          <option value="">—</option>
                          {deviceTypes.map((t) => (
                            <option key={t.key} value={t.key}>
                              {t.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <select
                          value={r.device_role}
                          onChange={(e) => updateRow(i, { device_role: e.target.value })}
                        >
                          <option value="">—</option>
                          {deviceRoles.map((role) => (
                            <option key={role.key} value={role.key}>
                              {role.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <select
                          value={r.network_zone}
                          onChange={(e) => updateRow(i, { network_zone: e.target.value as "" | NetworkZone })}
                        >
                          <option value="">—</option>
                          <option value="it">IT</option>
                          <option value="ot">OT</option>
                        </select>
                      </td>
                      <td>
                        <input value={r.site} onChange={(e) => updateRow(i, { site: e.target.value })} />
                      </td>
                      <td>
                        <button className="link-button danger" onClick={() => removeRow(i)}>
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={8} className="empty-state">
                        No rows left - go back and paste some hostnames.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </>
          )}
        </div>
        <div className="modal-footer">
          {rows === null ? (
            <button onClick={handleParse} disabled={parsing}>
              {parsing ? "Detecting…" : "Next: review devices"}
            </button>
          ) : (
            <>
              <button className="button-like" style={{ marginRight: 8 }} onClick={() => setRows(null)}>
                Back
              </button>
              <button onClick={handleSubmit} disabled={submitting || rows.length === 0}>
                {submitting ? "Adding…" : `Add ${rows.length} device${rows.length === 1 ? "" : "s"}`}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
