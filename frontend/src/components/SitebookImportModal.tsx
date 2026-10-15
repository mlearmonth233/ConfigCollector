import { useState } from "react";

import { extractErrorMessage } from "../api/client";
import { devicesApi } from "../api/resources";
import type { DeviceImportResult, DeviceRole, DeviceType, SitebookImport } from "../api/types";
import { DeviceTypeOptions } from "./DeviceTypeOptions";

interface Row {
  key: string;
  include: boolean;
  name: string;
  host: string;
  port: string;
  device_type: string;
  device_role: string;
  site: string;
  location: string | null;
  pid: string | null;
  serial: string | null;
  stack_members: string[];
  exists: boolean;
}

interface Props {
  deviceTypes: DeviceType[];
  deviceRoles: DeviceRole[];
  onClose: () => void;
  onDone: (result: DeviceImportResult) => void;
}

/** Add devices from an IT/OT Sitebook workbook: its "Devices and Serials"
 * tab gives the hostname (column A) and IP (column C) of every device;
 * rows sharing an IP are switch-stack members and become one device. Every
 * row is reviewed here before anything is saved. */
export function SitebookImportModal({ deviceTypes, deviceRoles, onClose, onDone }: Props) {
  const [book, setBook] = useState<SitebookImport | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [siteForAll, setSiteForAll] = useState("");
  const [reading, setReading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    setReading(true);
    try {
      const { data } = await devicesApi.readSitebook(file);
      setBook(data);
      setRows(
        data.devices.map((d) => ({
          key: `${d.row}-${d.name}`,
          include: !d.existing_device_id,
          name: d.name,
          host: d.host,
          port: "22",
          device_type: d.suggested_device_type ?? "",
          device_role: d.device_role ?? "",
          site: "",
          location: d.location,
          pid: d.pid,
          serial: d.serial,
          stack_members: d.stack_members,
          exists: !!d.existing_device_id,
        })),
      );
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setReading(false);
    }
  }

  function update(index: number, patch: Partial<Row>) {
    setRows((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  const chosen = rows.filter((r) => r.include);
  const missingType = chosen.filter((r) => !r.device_type).length;

  async function handleSubmit() {
    setError(null);
    setSubmitting(true);
    try {
      const results = await Promise.allSettled(
        chosen.map((r) =>
          devicesApi.create({
            name: r.name,
            host: r.host,
            port: Number(r.port) || 22,
            device_type: r.device_type || undefined,
            device_role: r.device_role || undefined,
            site: (r.site || siteForAll).trim() || undefined,
          }),
        ),
      );
      onDone({
        created: results.filter((r) => r.status === "fulfilled").length,
        errors: results
          .map((r, i) => (r.status === "rejected" ? `${chosen[i].name}: ${extractErrorMessage(r.reason)}` : null))
          .filter((e): e is string => e !== null),
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" style={{ width: "min(1250px, 96vw)" }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Import devices from an IT OT Sitebook</h2>
          <div className="modal-header-actions">
            <button onClick={onClose}>Cancel</button>
          </div>
        </div>
        <div className="modal-body">
          {error && <div className="error-banner">{error}</div>}
          {book === null ? (
            <>
              <p className="page-subtitle" style={{ marginTop: 0 }}>
                Choose the site's IT OT Sitebook (.xlsx). Its <strong>Devices and Serials</strong> tab is read: hostname from column A,
                IP from column C. Rows with the same IP as an earlier row are switch-stack members and are added as one device.
                Device type and role are suggested from your naming rules or the PID; you review everything before it is saved.
              </p>
              <input type="file" accept=".xlsx,.xlsm" disabled={reading} onChange={(e) => void handleFile(e.target.files?.[0])} />
              {reading && <span className="table-hint"> Reading…</span>}
            </>
          ) : (
            <>
              <p className="page-subtitle" style={{ marginTop: 0 }}>
                {book.devices.length} device(s) on the <em>{book.sheet}</em> tab
                {book.skipped.length ? `, ${book.skipped.length} row(s) skipped` : ""}. Devices already in Packrat (same name or IP) are
                unticked.
              </p>
              <label style={{ maxWidth: 360 }}>
                Site for every device (optional)
                <input value={siteForAll} onChange={(e) => setSiteForAll(e.target.value)} placeholder="e.g. Andover" />
              </label>
              <table className="data-table" style={{ marginTop: 10 }}>
                <thead>
                  <tr>
                    <th>
                      <input
                        type="checkbox"
                        aria-label="Select all"
                        checked={rows.length > 0 && rows.every((r) => r.include)}
                        onChange={(e) => setRows((prev) => prev.map((r) => ({ ...r, include: e.target.checked })))}
                      />
                    </th>
                    <th>Name</th>
                    <th>IP</th>
                    <th>Type</th>
                    <th>Role</th>
                    <th>Site</th>
                    <th>From the Sitebook</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={r.key} style={r.include ? undefined : { opacity: 0.55 }}>
                      <td>
                        <input type="checkbox" checked={r.include} onChange={(e) => update(i, { include: e.target.checked })} />
                      </td>
                      <td>
                        <input value={r.name} onChange={(e) => update(i, { name: e.target.value })} />
                        {r.exists && <div className="table-hint">already in Packrat</div>}
                      </td>
                      <td>
                        <input value={r.host} onChange={(e) => update(i, { host: e.target.value })} style={{ width: 130 }} />
                      </td>
                      <td>
                        <select value={r.device_type} onChange={(e) => update(i, { device_type: e.target.value })}>
                          <option value="">—</option>
                          <DeviceTypeOptions deviceTypes={deviceTypes} />
                        </select>
                      </td>
                      <td>
                        <select value={r.device_role} onChange={(e) => update(i, { device_role: e.target.value })}>
                          <option value="">—</option>
                          {deviceRoles.map((role) => (
                            <option key={role.key} value={role.key}>
                              {role.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input value={r.site} onChange={(e) => update(i, { site: e.target.value })} placeholder={siteForAll || undefined} style={{ width: 120 }} />
                      </td>
                      <td className="table-hint">
                        {[r.pid, r.location, r.serial ? `SN ${r.serial}` : null].filter(Boolean).join(" · ")}
                        {r.stack_members.length > 0 && <div>Stack of {r.stack_members.length + 1}: also {r.stack_members.join(", ")}</div>}
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={7} className="empty-state">
                        No devices with a hostname and IP on that tab.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
              {book.skipped.length > 0 && (
                <details style={{ marginTop: 10 }}>
                  <summary className="table-hint">{book.skipped.length} row(s) not added, and why</summary>
                  <table className="data-table">
                    <tbody>
                      {book.skipped.map((s) => (
                        <tr key={s.row}>
                          <td>row {s.row}</td>
                          <td>{s.name ?? "—"}</td>
                          <td>{s.host ?? "—"}</td>
                          <td className="table-hint">{s.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              )}
            </>
          )}
        </div>
        <div className="modal-footer">
          {book !== null && (
            <>
              {missingType > 0 && (
                <span className="field-hint" style={{ marginRight: 12 }}>
                  {missingType} selected device(s) have no type: pick one, or they will be rejected unless the name identifies them.
                </span>
              )}
              <button className="button-like" style={{ marginRight: 8 }} onClick={() => setBook(null)}>
                Choose another file
              </button>
              <button onClick={handleSubmit} disabled={submitting || chosen.length === 0}>
                {submitting ? "Adding…" : `Add ${chosen.length} device${chosen.length === 1 ? "" : "s"}`}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
