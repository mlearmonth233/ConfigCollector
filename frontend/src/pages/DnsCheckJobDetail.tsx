import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { dnsCheckApi } from "../api/resources";
import type { DnsCheckJobDetail as DnsCheckJobDetailType } from "../api/types";
import { JobStatusSummary } from "../components/JobStatusSummary";
import { StatusBadge } from "../components/StatusBadge";

const ACTIVE_STATUSES = new Set(["pending", "running"]);

function Pill({ ok, okLabel, failLabel }: { ok: boolean | null; okLabel: string; failLabel: string }) {
  if (ok === null) return <span className="field-hint">—</span>;
  return <span className={`status-badge status-${ok ? "completed" : "failed"}`}>{ok ? okLabel : failLabel}</span>;
}

function looksLikeIpAddress(s: string): boolean {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(s) || s.includes(":");
}

export function DnsCheckJobDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<DnsCheckJobDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | undefined;

    async function load() {
      try {
        const { data } = await dnsCheckApi.getJob(jobId!);
        if (cancelled) return;
        setJob(data);
        if (!ACTIVE_STATUSES.has(data.status) && interval) {
          clearInterval(interval);
        }
      } catch (err) {
        if (!cancelled) setError(extractErrorMessage(err));
      }
    }

    void load();
    interval = setInterval(load, 3000);
    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
    };
  }, [jobId]);

  async function handleCancel() {
    if (!jobId) return;
    if (!confirm("Cancel this DNS check? Targets not yet checked are skipped; one already in flight finishes normally.")) {
      return;
    }
    setCancelError(null);
    setCancelling(true);
    try {
      const { data } = await dnsCheckApi.cancelJob(jobId);
      setJob(data);
    } catch (err) {
      setCancelError(extractErrorMessage(err));
    } finally {
      setCancelling(false);
    }
  }

  if (error) return <div className="page error-banner">{error}</div>;
  if (!job) return <div className="page">Loading…</div>;

  return (
    <div className="page">
      <p>
        <Link to="/dns-check">← Back to DNS Check</Link>
      </p>
      <div className="page-header-row sticky-header">
        <h1>DNS check {job.id.slice(0, 8)}</h1>
        <div className="page-actions">
          <StatusBadge status={job.status} />
          {ACTIVE_STATUSES.has(job.status) && (
            <button className="link-button danger" onClick={handleCancel} disabled={cancelling}>
              {cancelling ? "Cancelling…" : "Cancel"}
            </button>
          )}
        </div>
      </div>
      <p className="page-subtitle">
        Started {new Date(job.created_at).toLocaleString()}
        {job.finished_at && ` · Finished ${new Date(job.finished_at).toLocaleString()}`}
      </p>

      {cancelError && <div className="error-banner">{cancelError}</div>}

      <JobStatusSummary items={job.items} />

      <table className="data-table">
        <thead>
          <tr>
            <th>Target</th>
            <th>Status</th>
            <th>Ping</th>
            <th>Forward DNS</th>
            <th>Reverse DNS</th>
          </tr>
        </thead>
        <tbody>
          {job.items.map((item) => (
            <tr key={item.id}>
              <td>{item.target}</td>
              <td>
                <StatusBadge status={item.status} />
              </td>
              <td>
                <Pill ok={item.ping_ok} okLabel="Reachable" failLabel="No reply" />
              </td>
              <td>
                {item.forward_ok ? (
                  item.forward_ips.join(", ")
                ) : item.status === "completed" ? (
                  <span className="field-hint">
                    {looksLikeIpAddress(item.target) ? "N/A (already an IP)" : "no DNS record found"}
                  </span>
                ) : (
                  <span className="field-hint">—</span>
                )}
              </td>
              <td>
                {item.reverse_ok ? (
                  item.reverse_hostname
                ) : item.status === "completed" ? (
                  <span className="field-hint">no PTR record</span>
                ) : (
                  <span className="field-hint">—</span>
                )}
              </td>
            </tr>
          ))}
          {job.items.length === 0 && (
            <tr>
              <td colSpan={5} className="empty-state">
                No targets in this job.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
