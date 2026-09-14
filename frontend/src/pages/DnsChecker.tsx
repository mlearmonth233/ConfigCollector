import { useState } from "react";

import { extractErrorMessage } from "../api/client";
import { dnsCheckerApi } from "../api/resources";
import type { DnsCheckResult } from "../api/types";

function YesNoBadge({ ok }: { ok: boolean }) {
  return <span className={`status-badge status-${ok ? "completed" : "failed"}`}>{ok ? "Yes" : "No"}</span>;
}

export function DnsChecker() {
  const [input, setInput] = useState("");
  const [results, setResults] = useState<DnsCheckResult[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCheck() {
    setError(null);
    setChecking(true);
    try {
      const { data } = await dnsCheckerApi.check(input);
      setResults(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setChecking(false);
    }
  }

  const summary =
    results && results.length > 0
      ? {
          total: results.length,
          pingOk: results.filter((r) => r.ping_ok).length,
          dnsOk: results.filter((r) => r.dns_ok).length,
        }
      : null;

  return (
    <div className="page">
      <h1>DNS Checker</h1>
      <p className="page-subtitle">
        Paste a batch of hostnames and/or IP addresses (one per line, comma-separated, or a mix of
        both) to check ping-alive status, forward DNS resolution, and reverse DNS lookup for each -
        not tied to your saved devices, just a quick ad-hoc diagnostic.
      </p>
      {error && <div className="error-banner">{error}</div>}

      <div className="card-form" style={{ marginBottom: 24 }}>
        <label style={{ marginBottom: 14 }}>
          Hostnames / IP addresses
          <textarea
            rows={8}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={"sw1.example.com\n192.0.2.10\nsw2.example.com, 192.0.2.11"}
          />
        </label>
        <button onClick={handleCheck} disabled={checking || !input.trim()}>
          {checking ? "Checking…" : "Check"}
        </button>
      </div>

      {summary && (
        <p className="page-subtitle" style={{ marginTop: -12 }}>
          {summary.total} checked · {summary.pingOk} responded to ping · {summary.dnsOk} resolved in DNS
        </p>
      )}

      {results && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Input</th>
              <th>Type</th>
              <th>Resolved IP</th>
              <th>Reverse hostname</th>
              <th>DNS</th>
              <th>Ping</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r) => (
              <tr key={r.input}>
                <td>{r.input}</td>
                <td>{r.input_type === "ip" ? "IP" : "Hostname"}</td>
                <td>{r.resolved_ip ?? "—"}</td>
                <td>{r.reverse_hostname ?? "—"}</td>
                <td>
                  <YesNoBadge ok={r.dns_ok} />
                </td>
                <td>
                  <YesNoBadge ok={r.ping_ok} />
                </td>
                <td>{r.error ?? "—"}</td>
              </tr>
            ))}
            {results.length === 0 && (
              <tr>
                <td colSpan={7} className="empty-state">
                  No results.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
