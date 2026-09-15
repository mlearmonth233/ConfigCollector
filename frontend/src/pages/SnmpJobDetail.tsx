import { Fragment, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { snmpApi } from "../api/resources";
import type { SnmpJobDetail as SnmpJobDetailType, SnmpJobItem } from "../api/types";
import { JobStatusSummary } from "../components/JobStatusSummary";
import { LiveConsole } from "../components/LiveConsole";
import { StatusBadge } from "../components/StatusBadge";

const ACTIVE_STATUSES = new Set(["pending", "running"]);

function downloadReport(item: SnmpJobItem) {
  if (!item.report) return;
  const blob = new Blob([item.report], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${item.device_name.replace(/[^\w.-]+/g, "_")}_snmp.txt`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function SnmpJobDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<SnmpJobDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | undefined;

    async function load() {
      try {
        const { data } = await snmpApi.getJob(jobId!);
        if (cancelled) return;
        setJob(data);
        if (!ACTIVE_STATUSES.has(data.status) && interval) clearInterval(interval);
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

  function toggle(itemId: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  }

  async function handleCancel() {
    if (!jobId) return;
    if (!confirm("Cancel this SNMP poll? Devices not yet polled are skipped; ones in flight finish normally.")) return;
    setCancelError(null);
    setCancelling(true);
    try {
      const { data } = await snmpApi.cancelJob(jobId);
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
        <Link to="/snmp">← Back to SNMP</Link>
      </p>
      <div className="page-header-row sticky-header">
        <h1>SNMP poll {job.id.slice(0, 8)}</h1>
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
        {job.extra_oids.length > 0 && ` · Extra OIDs: ${job.extra_oids.join(", ")}`}
      </p>

      {cancelError && <div className="error-banner">{cancelError}</div>}

      <JobStatusSummary items={job.items} />

      <table className="data-table">
        <thead>
          <tr>
            <th>Device</th>
            <th>Profile</th>
            <th>Status</th>
            <th>Error</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {job.items.map((item) => (
            <Fragment key={item.id}>
              <tr>
                <td>
                  {item.device_name}
                  {item.device_host && <div className="field-hint">{item.device_host}</div>}
                </td>
                <td>{item.profile_name ?? "—"}</td>
                <td>
                  <StatusBadge status={item.status} />
                </td>
                <td>{item.error_message ?? "—"}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <button className="link-button" onClick={() => toggle(item.id)}>
                    {expanded.has(item.id) ? "Hide" : item.report ? "Report" : "Console"}
                  </button>
                  {item.report && (
                    <button className="link-button" style={{ marginLeft: 12 }} onClick={() => downloadReport(item)}>
                      Download
                    </button>
                  )}
                </td>
              </tr>
              {expanded.has(item.id) && (
                <tr className="console-row">
                  <td colSpan={5}>
                    <LiveConsole output={item.report ?? item.live_output} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
          {job.items.length === 0 && (
            <tr>
              <td colSpan={5} className="empty-state">
                No devices in this poll.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
