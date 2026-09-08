import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { jobsApi } from "../api/resources";
import type { Job } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

export function Jobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const { data } = await jobsApi.list();
        if (!cancelled) setJobs(data);
      } catch (err) {
        if (!cancelled) setError(extractErrorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    const interval = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="page">
      <h1>Collection Jobs</h1>
      {error && <div className="error-banner">{error}</div>}
      {loading ? (
        <p>Loading…</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Started</th>
              <th>Status</th>
              <th>Devices</th>
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
                  <Link to={`/jobs/${j.id}`}>View</Link>
                </td>
              </tr>
            ))}
            {jobs.length === 0 && (
              <tr>
                <td colSpan={4} className="empty-state">
                  No collection jobs yet. Start one from the Devices page.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
