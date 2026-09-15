import { Fragment, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { firmwareApi } from "../api/resources";
import type { FirmwareJobDetail as FirmwareJobDetailType } from "../api/types";
import { JobStatusSummary } from "../components/JobStatusSummary";
import { LiveConsole } from "../components/LiveConsole";
import { StatusBadge } from "../components/StatusBadge";

const ACTIVE_STATUSES = new Set(["pending", "running"]);

export function FirmwareJobDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<FirmwareJobDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedConsoles, setExpandedConsoles] = useState<Set<string>>(new Set());
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  function toggleConsole(itemId: string) {
    setExpandedConsoles((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  }

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | undefined;

    async function load() {
      try {
        const { data } = await firmwareApi.getJob(jobId!);
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
    if (!confirm("Cancel this upgrade job? Devices not yet started are skipped; one already mid-upgrade finishes normally.")) {
      return;
    }
    setCancelError(null);
    setCancelling(true);
    try {
      const { data } = await firmwareApi.cancelJob(jobId);
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
        <Link to="/firmware">← Back to firmware</Link>
      </p>
      <div className="page-header-row sticky-header">
        <h1>Upgrade job {job.id.slice(0, 8)}</h1>
        <div className="page-actions">
          <StatusBadge status={job.status} />
          {ACTIVE_STATUSES.has(job.status) && (
            <button className="link-button danger" onClick={handleCancel} disabled={cancelling}>
              {cancelling ? "Cancelling…" : "Cancel job"}
            </button>
          )}
        </div>
      </div>
      <p className="page-subtitle">
        Protocol: {job.protocol.toUpperCase()} · Started {new Date(job.created_at).toLocaleString()}
        {job.finished_at && ` · Finished ${new Date(job.finished_at).toLocaleString()}`}
      </p>

      {cancelError && <div className="error-banner">{cancelError}</div>}

      <JobStatusSummary items={job.items} />

      {job.items.some((i) => i.status === "authenticating") && (
        <div className="info-banner">
          One or more devices are logging in - if their credential uses push-based MFA, approve the
          request on your phone now. No upgrade commands run until it succeeds.
        </div>
      )}

      <table className="data-table">
        <thead>
          <tr>
            <th>Device</th>
            <th>Status</th>
            <th>Error</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {job.items.map((item) => (
            <Fragment key={item.id}>
              <tr>
                <td>{item.device_name}</td>
                <td>
                  <StatusBadge status={item.status} />
                </td>
                <td>{item.error_message ?? "—"}</td>
                <td>
                  <button className="link-button" onClick={() => toggleConsole(item.id)}>
                    {expandedConsoles.has(item.id) ? "Hide console" : "Console"}
                  </button>
                </td>
              </tr>
              {expandedConsoles.has(item.id) && (
                <tr className="console-row">
                  <td colSpan={4}>
                    <LiveConsole output={item.live_output} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
          {job.items.length === 0 && (
            <tr>
              <td colSpan={4} className="empty-state">
                No devices in this job.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
