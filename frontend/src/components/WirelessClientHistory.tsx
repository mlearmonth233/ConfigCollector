import { useState } from "react";

import { extractErrorMessage } from "../api/client";
import type { ClientEventKind, ClientHistory, ClientLookup } from "../api/wirelessAudit";
import { wirelessAuditApi } from "../api/wirelessAudit";

const KIND_LABEL: Record<ClientEventKind, string> = {
  join: "Join",
  roam: "Roam",
  drop: "Drop",
  auth_failure: "Auth failure",
  dhcp_failure: "DHCP failure",
  exclusion: "Excluded",
  timeout: "Timeout",
  other: "Other",
};

const COUNTER_LABEL: Record<string, string> = {
  retries: "Data retries",
  rts_retries: "RTS retries",
  packets_sent: "Packets sent",
  packets_received: "Packets received",
  decrypt_failed: "Decrypt failures",
  mic_failed: "MIC failures",
  tx_dropped: "Tx dropped",
  excessive_retries: "Excessive retries",
  move_count: "Mobility moves",
};

/** One client's story: issues, roam path, counters and every log line. */
export function ClientTimeline({ history }: { history: ClientHistory }) {
  const [kinds, setKinds] = useState<Set<ClientEventKind>>(new Set());
  const events = history.events.filter((e) => kinds.size === 0 || kinds.has(e.kind));
  const present = Array.from(new Set(history.events.map((e) => e.kind)));
  return (
    <div className="client-history">
      <div className="client-history-head">
        <strong>{history.mac}</strong> on {history.controller_name}
        {history.connected ? "" : " · no longer associated"}
        <span className="table-hint">
          {" "}
          · {history.roams} roam(s) · {history.drops} drop(s) · {history.auth_failures} auth failure(s) · {history.dhcp_failures} DHCP failure(s)
          {history.exclusions ? ` · excluded ${history.exclusions}×` : ""}
        </span>
      </div>
      {history.issues.length > 0 && (
        <ul className="audit-list">
          {history.issues.map((i, n) => (
            <li key={n} className="audit-bad">
              {i}
            </li>
          ))}
        </ul>
      )}
      {history.roam_history.length > 0 && (
        <p className="client-roam-path">
          <span className="table-hint">Roam path: </span>
          {history.roam_history.map((r, i) => (
            <span key={i}>
              {i > 0 ? " → " : ""}
              <span title={[r.time, r.type].filter(Boolean).join(" · ")}>{r.ap}</span>
            </span>
          ))}
        </p>
      )}
      {Object.keys(history.counters).length > 0 && (
        <p className="table-hint">
          {Object.entries(history.counters)
            .map(([k, v]) => `${COUNTER_LABEL[k] ?? k}: ${v}`)
            .join(" · ")}
        </p>
      )}
      {history.events.length > 0 && (
        <>
          <div className="audit-filters" style={{ margin: "6px 0" }}>
            {present.map((k) => (
              <label key={k} className="inline-check">
                <input
                  type="checkbox"
                  checked={kinds.has(k)}
                  onChange={(e) =>
                    setKinds((prev) => {
                      const next = new Set(prev);
                      if (e.target.checked) next.add(k);
                      else next.delete(k);
                      return next;
                    })
                  }
                />{" "}
                {KIND_LABEL[k]} ({history.events.filter((e) => e.kind === k).length})
              </label>
            ))}
          </div>
          <table className="data-table audit-subtable">
            <thead>
              <tr>
                <th>Time</th>
                <th>Event</th>
                <th>Reason</th>
                <th>Log line</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={i}>
                  <td className="audit-mono">{e.time ?? "—"}</td>
                  <td>
                    <span className={`client-event client-event-${e.kind}`}>{KIND_LABEL[e.kind]}</span>
                  </td>
                  <td>{e.reason ?? "—"}</td>
                  <td className="audit-mono table-hint">{e.text}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

/** Search every controller's recent snapshots for one MAC, and collect
 * that client's detail, roam history and log lines on demand. */
export function ClientLookupPanel({ onInvestigate }: { onInvestigate: (mac: string, commands: Record<string, string[]>) => void }) {
  const [mac, setMac] = useState("");
  const [days, setDays] = useState(7);
  const [result, setResult] = useState<ClientLookup | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function search() {
    if (!mac.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setResult((await wirelessAuditApi.lookupClient(mac.trim(), days)).data);
    } catch (err) {
      setError(extractErrorMessage(err));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card-form" style={{ marginTop: 12 }}>
      <h2 style={{ marginTop: 0 }}>Client lookup</h2>
      <p className="page-subtitle">
        Any MAC spelling (aabb.ccdd.eeff, aa:bb:cc:dd:ee:ff). Searches the controllers' snapshots for every log line, roam and counter about
        that client. <em>Investigate on the controllers</em> collects its detail, roam history and log lines now.
      </p>
      <div className="audit-filters">
        <input value={mac} onChange={(e) => setMac(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void search()} placeholder="Client MAC address" style={{ width: 240 }} />
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Search back">
          <option value={1}>Last day</option>
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
        </select>
        <button onClick={() => void search()} disabled={busy || !mac.trim()}>
          {busy ? "Searching…" : "Search"}
        </button>
        {result && (
          <button className="link-button" onClick={() => onInvestigate(result.mac, result.commands)}>
            Investigate on the controllers
          </button>
        )}
      </div>
      {error && <div className="error-banner" style={{ marginTop: 10 }}>{error}</div>}
      {result && (
        <div style={{ marginTop: 10 }}>
          <p className="table-hint">
            {result.mac}: searched {result.snapshots_searched} snapshot(s).{" "}
            {result.histories.length === 0 ? "No log lines, roams or counters for this client. Use Investigate to collect them now." : ""}
          </p>
          {result.histories.map((h) => (
            <ClientTimeline key={h.controller_id} history={h} />
          ))}
        </div>
      )}
    </div>
  );
}
