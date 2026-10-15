import { useMemo, useState } from "react";

import { extractErrorMessage } from "../api/client";
import { devicesApi } from "../api/resources";
import type { Device } from "../api/types";
import { sortByDeviceName } from "../utils/deviceNameSort";

const WLC_TYPES = new Set(["cisco_wlc", "cisco_wlc_9800"]);

/** RFC 1918 or carrier-grade NAT (100.64.0.0/10). */
export function isPrivateIp(host: string): boolean {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host.trim());
  if (!m) return false;
  const [a, b] = [Number(m[1]), Number(m[2])];
  return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127);
}

/** Remove the devices on private addresses (typically OT switches behind
 * a cell/area firewall, whose logs are collected some other way). Every
 * one is listed for review; wireless controllers start unticked. */
export function RemovePrivateDevicesModal({ devices, onClose, onDone }: { devices: Device[]; onClose: () => void; onDone: (removed: number, errors: string[]) => void }) {
  const candidates = useMemo(() => sortByDeviceName(devices.filter((d) => isPrivateIp(d.host)), (d) => d.name), [devices]);
  const [selected, setSelected] = useState<Set<string>>(() => new Set(candidates.filter((d) => !WLC_TYPES.has(d.device_type)).map((d) => d.id)));
  const [busy, setBusy] = useState(false);

  async function handleRemove() {
    if (!confirm(`Remove ${selected.size} device(s)? Their collected snapshots are kept but detached.`)) return;
    setBusy(true);
    const chosen = candidates.filter((d) => selected.has(d.id));
    const results = await Promise.allSettled(chosen.map((d) => devicesApi.remove(d.id)));
    setBusy(false);
    onDone(
      results.filter((r) => r.status === "fulfilled").length,
      results.map((r, i) => (r.status === "rejected" ? `${chosen[i].name}: ${extractErrorMessage(r.reason)}` : null)).filter((e): e is string => e !== null),
    );
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" style={{ width: "min(900px, 95vw)" }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Remove devices with private IP addresses</h2>
          <div className="modal-header-actions">
            <button onClick={onClose}>Cancel</button>
          </div>
        </div>
        <div className="modal-body">
          <p className="page-subtitle" style={{ marginTop: 0 }}>
            Devices whose IP is private (10.x, 172.16-31.x, 192.168.x, 100.64-127.x) - usually OT switches whose logs are collected
            elsewhere. Untick any to keep. Wireless controllers start unticked.
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    aria-label="Select all"
                    checked={candidates.length > 0 && selected.size === candidates.length}
                    onChange={(e) => setSelected(new Set(e.target.checked ? candidates.map((d) => d.id) : []))}
                  />
                </th>
                <th>Name</th>
                <th>IP</th>
                <th>Type</th>
                <th>Site</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((d) => (
                <tr key={d.id}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.has(d.id)}
                      onChange={(e) =>
                        setSelected((prev) => {
                          const next = new Set(prev);
                          if (e.target.checked) next.add(d.id);
                          else next.delete(d.id);
                          return next;
                        })
                      }
                    />
                  </td>
                  <td>{d.name}</td>
                  <td>{d.host}</td>
                  <td>{d.device_type}</td>
                  <td>{d.site ?? "—"}</td>
                </tr>
              ))}
              {candidates.length === 0 && (
                <tr>
                  <td colSpan={5} className="empty-state">
                    No devices with a private IP address.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="modal-footer">
          <button onClick={() => void handleRemove()} disabled={busy || selected.size === 0}>
            {busy ? "Removing…" : `Remove ${selected.size} device${selected.size === 1 ? "" : "s"}`}
          </button>
        </div>
      </div>
    </div>
  );
}
