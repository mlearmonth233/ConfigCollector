import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { jobsApi } from "../api/resources";
import type { JobDetail as JobDetailType } from "../api/types";
import { SnapshotModal } from "../components/SnapshotModal";
import { StatusBadge } from "../components/StatusBadge";

const ACTIVE_STATUSES = new Set(["pending", "running"]);

export function JobDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<JobDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openSnapshotId, setOpenSnapshotId] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | undefined;

    async function load() {
      try {
        const { data } = await jobsApi.get(jobId!);
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

  if (error) return <div className="page error-banner">{error}</div>;
  if (!job) return <div className="page">Loading…</div>;

  return (
    <div className="page">
      <p>
        <Link to="/jobs">← Back to jobs</Link>
      </p>
      <div className="page-header-row">
        <h1>Job {job.id.slice(0, 8)}</h1>
        <StatusBadge status={job.status} />
      </div>
      <p className="page-subtitle">
        Started {new Date(job.created_at).toLocaleString()}
        {job.finished_at && ` · Finished ${new Date(job.finished_at).toLocaleString()}`}
      </p>

      {job.items.some((i) => i.status === "authenticating") && (
        <div className="info-banner">
          One or more devices are logging in - if their credential uses push-based MFA, approve the
          request on your phone now. This can take up to a minute; no commands run until it succeeds.
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
            <tr key={item.id}>
              <td>{item.device_name}</td>
              <td>
                <StatusBadge status={item.status} />
                {item.used_fallback_credential && (
                  <span className="status-badge status-fallback" title="Primary credential failed; the fallback credential was used instead">
                    fallback used
                  </span>
                )}
              </td>
              <td>{item.error_message ?? "—"}</td>
              <td>
                {item.snapshot_id && (
                  <button className="link-button" onClick={() => setOpenSnapshotId(item.snapshot_id)}>
                    View config
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {openSnapshotId && (
        <SnapshotModal snapshotId={openSnapshotId} onClose={() => setOpenSnapshotId(null)} />
      )}
    </div>
  );
}
