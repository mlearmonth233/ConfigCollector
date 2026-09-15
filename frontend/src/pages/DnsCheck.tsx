import { useState } from "react";

import { extractErrorMessage } from "../api/client";
import { dnsCheckApi } from "../api/resources";
import type { DnsCheckResult } from "../api/types";

function parseTargets(text: string): string[] {
  return Array.from(
    new Set(
      text
        .split(/[\r\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean)
    )
  );
}

// Display-only heuristic (the backend does the real check) - just decides
// whether a failed forward lookup means "no DNS record" or "there was
// nothing to resolve, this is already an IP".
function looksLikeIpAddress(s: string): boolean {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(s) || s.includes(":");
}

function Pill({ ok, okLabel, failLabel }: { ok: boolean; okLabel: string; failLabel: string }) {
  return <span className={`status-badge status-${ok ? "completed" : "failed"}`}>{ok ? okLabel : failLabel}</span>;
}

export function DnsCheck() {
  const [text, setText] = useState("");
  const [results, setResults] = useState<DnsCheckResult[] | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const targets = parseTargets(text);

  async function handleRun() {
    setError(null);
    if (targets.length === 0) {
      setError("Paste at least one hostname or IP first.");
      return;
    }
    setRunning(true);
    setResults(null);
    try {
      const { data } = await dnsCheckApi.run(targets);
      setResults(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="page">
      <h1>DNS Check</h1>
      <p className="page-subtitle">
        Paste a list of hostnames or IPs (one per line, or comma-separated) to bulk-check each one:
        whether it answers a ping, whether its hostname resolves in DNS (forward lookup), and whether
        its IP has a PTR record (reverse lookup). Nothing here is saved - it's a one-off diagnostic,
        not tied to your device inventory. As with the reachability check on the Devices page, a
        failed ping or missing DNS record doesn't necessarily mean a device is down - a firewall
        commonly drops ICMP, and plenty of reachable devices have no PTR record at all.
      </p>
      {error && <div className="error-banner">{error}</div>}

      <label>
        Hostnames or IPs
        <textarea
          rows={8}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={"core-sw1\n10.0.0.5\nwlc-01.example.com"}
          style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
        />
      </label>

      <div className="page-actions" style={{ marginTop: 12 }}>
        <button onClick={handleRun} disabled={running || targets.length === 0}>
          {running
            ? "Running…"
            : `Run checks${targets.length > 0 ? ` (${targets.length} target${targets.length === 1 ? "" : "s"})` : ""}`}
        </button>
      </div>

      {results && (
        <table className="data-table" style={{ marginTop: 24 }}>
          <thead>
            <tr>
              <th>Target</th>
              <th>Ping</th>
              <th>Forward DNS</th>
              <th>Reverse DNS</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r) => (
              <tr key={r.target}>
                <td>{r.target}</td>
                <td>
                  <Pill ok={r.ping_ok} okLabel="Reachable" failLabel="No reply" />
                </td>
                <td>
                  {r.forward_ok ? (
                    r.forward_ips.join(", ")
                  ) : (
                    <span className="field-hint">
                      {looksLikeIpAddress(r.target) ? "N/A (already an IP)" : "no DNS record found"}
                    </span>
                  )}
                </td>
                <td>
                  {r.reverse_ok ? (
                    r.reverse_hostname
                  ) : (
                    <span className="field-hint">no PTR record</span>
                  )}
                </td>
              </tr>
            ))}
            {results.length === 0 && (
              <tr>
                <td colSpan={4} className="empty-state">
                  No targets checked.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
