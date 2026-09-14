import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { firmwareJobsApi } from "../api/resources";
import type { FirmwareJob, FirmwareJobStatus } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

const ACTIVE_STATUSES = new Set<FirmwareJobStatus>([
  "pending",
  "running",
  "authenticating",
  "transferring",
  "verifying",
  "reloading",
]);
const ACTIVE_POLL_MS = 5000;
const IDLE_POLL_MS = 20000;

export function FirmwareJobs() {
  const [jobs, setJobs] = useState<FirmwareJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;

    async function load() {
      try {
        const { data } = await firmwareJobsApi.list();
        if (cancelled) return;
        setJobs(data);
        const hasActive = data.some((j) => ACTIVE_STATUSES.has(j.status));
        timeout = setTimeout(load, hasActive ? ACTIVE_POLL_MS : IDLE_POLL_MS);
      } catch (err) {
        if (cancelled) return;
        setError(extractErrorMessage(err));
        timeout = setTimeout(load, IDLE_POLL_MS);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
      if (timeout) clearTimeout(timeout);
    };
  }, []);

  async function handleDelete(jobId: string) {
    if (!confirm("Delete this firmware push job? Its history and console output go with it.")) return;
    setError(null);
    setDeletingId(jobId);
    try {
      await firmwareJobsApi.remove(jobId);
      setJobs((prev) => prev.filter((j) => j.id !== jobId));
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="page">
      <h1>Firmware Jobs</h1>
      {error && <div className="error-banner">{error}</div>}

      {loading ? (
        <p>Loading…</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Started</th>
              <th>Firmware</th>
              <th>Status</th>
              <th>Devices</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>{new Date(j.created_at).toLocaleString()}</td>
                <td>{j.firmware_filename}</td>
                <td>
                  <StatusBadge status={j.status} />
                </td>
                <td>{j.item_count}</td>
                <td>
                  <Link to={`/firmware-jobs/${j.id}`}>View</Link>
                  {!ACTIVE_STATUSES.has(j.status) && (
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
                <td colSpan={5} className="empty-state">
                  No firmware pushes yet. Start one from the Firmware page.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
