import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { devicesApi, dnsCheckApi } from "../api/resources";
import type { DnsCheckJob } from "../api/types";
import { sortByDeviceName } from "../utils/deviceNameSort";
import { StatusBadge } from "../components/StatusBadge";

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

export function DnsCheck() {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<DnsCheckJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const [text, setText] = useState("");
  const [deviceHosts, setDeviceHosts] = useState<string[]>([]);
  const [starting, setStarting] = useState(false);
  const targets = parseTargets(text);

  async function refresh() {
    setLoading(true);
    try {
      const { data } = await dnsCheckApi.listJobs();
      setJobs(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    // Start from every managed device's address - the usual thing to check
    // - so the box is never empty; the user can edit or replace the list.
    devicesApi
      .list()
      .then((r) => {
        const hosts = Array.from(new Set(sortByDeviceName(r.data, (d) => d.name).map((d) => d.host.trim()).filter(Boolean)));
        setDeviceHosts(hosts);
        setText((current) => (current.trim() ? current : hosts.join("\n")));
      })
      .catch(() => undefined);
  }, []);

  async function handleStart() {
    setError(null);
    if (targets.length === 0) {
      setError("Paste at least one hostname or IP first.");
      return;
    }
    setStarting(true);
    try {
      const { data } = await dnsCheckApi.createJob(targets);
      navigate(`/dns-check/jobs/${data.id}`);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setStarting(false);
    }
  }

  async function handleDelete(jobId: string) {
    if (!confirm("Delete this DNS check job? Its results go with it.")) return;
    setError(null);
    setDeletingId(jobId);
    try {
      await dnsCheckApi.removeJob(jobId);
      setJobs((prev) => prev.filter((j) => j.id !== jobId));
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setDeletingId(null);
    }
  }

  async function handleClearFinished() {
    if (!confirm("Clear all finished DNS check jobs? A job still in progress is left alone.")) return;
    setError(null);
    setNotice(null);
    setClearing(true);
    try {
      const { data } = await dnsCheckApi.clearFinished();
      await refresh();
      setNotice(`Cleared ${data.deleted} finished job(s).`);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setClearing(false);
    }
  }

  return (
    <div className="page">
      <h1>DNS Check</h1>
      <p className="page-subtitle">
        Paste a list of hostnames or IPs (one per line, or comma-separated) to bulk-check each one:
        whether it answers a ping, whether its hostname resolves in DNS (forward lookup), and whether
        its IP has a PTR record (reverse lookup). As with the reachability check on the Devices page, a
        failed ping or missing DNS record doesn't necessarily mean a device is down - a firewall
        commonly drops ICMP, and plenty of reachable devices have no PTR record at all. Runs as a
        background job (like Jobs/Firmware), so a paste of thousands of targets is fine.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="info-banner">{notice}</div>}

      <label>
        Hostnames or IPs
        {deviceHosts.length > 0 && (
          <span className="field-hint" style={{ marginLeft: 8 }}>
            (prefilled with all {deviceHosts.length} devices -{" "}
            <button type="button" className="link-button" style={{ padding: 0, fontSize: "inherit" }} onClick={() => setText(deviceHosts.join("\n"))}>
              reset to devices
            </button>{" "}
            /{" "}
            <button type="button" className="link-button" style={{ padding: 0, fontSize: "inherit" }} onClick={() => setText("")}>
              clear
            </button>
            )
          </span>
        )}
        <textarea
          rows={8}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={"core-sw1\n10.0.0.5\nwlc-01.example.com"}
          style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
        />
      </label>

      <div className="page-actions" style={{ marginTop: 12 }}>
        <button onClick={handleStart} disabled={starting || targets.length === 0}>
          {starting
            ? "Starting…"
            : `Run checks${targets.length > 0 ? ` (${targets.length} target${targets.length === 1 ? "" : "s"})` : ""}`}
        </button>
      </div>

      <div className="page-header-row" style={{ marginTop: 32 }}>
        <h2 style={{ marginBottom: 0 }}>Past checks</h2>
        {jobs.length > 0 && (
          <button className="link-button danger" disabled={clearing} onClick={handleClearFinished}>
            {clearing ? "Clearing…" : "Clear all finished jobs"}
          </button>
        )}
      </div>

      {loading ? (
        <p>Loading…</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Started</th>
              <th>Status</th>
              <th>Targets</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>{new Date(j.created_at).toLocaleString()}</td>
                <td>
                  <StatusBadge status={j.status} />
                </td>
                <td>{j.item_count}</td>
                <td>
                  <Link to={`/dns-check/jobs/${j.id}`}>View</Link>
                  {j.status !== "running" && (
                    <button
                      className="link-button danger"
                      style={{ marginLeft: 12 }}
                      disabled={deletingId === j.id}
                      onClick={() => handleDelete(j.id)}
                    >
                      {deletingId === j.id ? "Deleting…" : "Delete"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {jobs.length === 0 && (
              <tr>
                <td colSpan={4} className="empty-state">
                  No DNS checks yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
