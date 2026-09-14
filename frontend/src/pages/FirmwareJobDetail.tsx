import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { firmwareJobsApi } from "../api/resources";
import type { FirmwareJobDetail as FirmwareJobDetailType, FirmwareJobStatus } from "../api/types";
import { FirmwareJobStatusSummary } from "../components/FirmwareJobStatusSummary";
import { LiveConsole } from "../components/LiveConsole";
import { StatusBadge } from "../components/StatusBadge";

const ACTIVE_STATUSES = new Set<FirmwareJobStatus>([
  "pending",
  "running",
  "authenticating",
  "transferring",
  "verifying",
  "reloading",
]);

export function FirmwareJobDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<FirmwareJobDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedConsoles, setExpandedConsoles] = useState<Set<string>>(new Set());
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  async function handleCancel() {
    if (!jobId) return;
    if (
      !confirm(
        "Cancel this firmware job? Devices that haven't started yet will be skipped. A device " +
          "already mid-transfer can't be interrupted (a live SCP transfer is opaque until it " +
          "finishes) - it completes normally before the queue stops."
      )
    ) {
      return;
    }
    setCancelError(null);
    setCancelling(true);
    try {
      const { data } = await firmwareJobsApi.cancel(jobId);
      setJob(data);
    } catch (err) {
      setCancelError(extractErrorMessage(err));
    } finally {
      setCancelling(false);
    }
  }

  function toggleConsole(itemId: string) {
    setExpandedConsoles((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  }

  function expandAllConsoles(items: FirmwareJobDetailType["items"]) {
    setExpandedConsoles(new Set(items.map((i) => i.id)));
  }

  const hasAutoExpanded = useRef(false);
  const wasActive = useRef(false);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | undefined;

    async function load() {
      try {
        const { data } = await firmwareJobsApi.get(jobId!);
        if (cancelled) return;
        setJob(data);
        const isActive = ACTIVE_STATUSES.has(data.status);
        if (!hasAutoExpanded.current && isActive) {
          hasAutoExpanded.current = true;
          expandAllConsoles(data.items);
        }
        if (wasActive.current && !isActive) {
          setExpandedConsoles(new Set());
        }
        wasActive.current = isActive;
        if (!isActive && interval) {
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

  if (error) return <div className="page error-banner">{error}</div>;
  if (!job) return <div className="page">Loading…</div>;

  return (
    <div className="page">
      <p>
        <Link to="/firmware-jobs">← Back to firmware jobs</Link>
      </p>
      <div className="page-header-row sticky-header">
        <h1>Firmware push {job.id.slice(0, 8)}</h1>
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
        {job.firmware_filename} → flash:{job.target_filename} · Started {new Date(job.created_at).toLocaleString()}
        {job.finished_at && ` · Finished ${new Date(job.finished_at).toLocaleString()}`}
      </p>

      {cancelError && <div className="error-banner">{cancelError}</div>}

      <FirmwareJobStatusSummary items={job.items} />

      {job.items.length > 1 && (
        <p style={{ marginTop: -8 }}>
          <button
            className="link-button"
            onClick={() =>
              expandedConsoles.size === job.items.length
                ? setExpandedConsoles(new Set())
                : expandAllConsoles(job.items)
            }
          >
            {expandedConsoles.size === job.items.length ? "Collapse all consoles" : "Expand all consoles"}
          </button>
        </p>
      )}

      {job.items.some((i) => i.status === "authenticating") && (
        <div className="info-banner">
          One or more devices are logging in - if their credential uses push-based MFA, approve the
          request on your phone now.
        </div>
      )}

      <table className="data-table">
        <thead>
          <tr>
            <th>Device</th>
            <th>Status</th>
            <th>Checksum</th>
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
                  {item.used_fallback_credential && (
                    <span
                      className="status-badge status-fallback"
                      title="Primary credential failed; the fallback credential was used instead"
                    >
                      fallback used
                    </span>
                  )}
                </td>
                <td>
                  {item.checksum_verified === null
                    ? "—"
                    : item.checksum_verified
                      ? "Verified"
                      : "Mismatch"}
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
                  <td colSpan={5}>
                    <LiveConsole output={item.live_output} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
