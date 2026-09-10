import type { JobItem, JobStatus } from "../api/types";

const STATUS_ORDER: JobStatus[] = [
  "pending",
  "authenticating",
  "running",
  "completed",
  "failed",
  "cancelled",
];
const STATUS_LABELS: Record<JobStatus, string> = {
  pending: "Pending",
  authenticating: "Authenticating",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

/** A segmented progress bar + legend showing, at a glance, how many
 * devices in a job are queued, logging in, running commands, done, or
 * failed - proportional to the item count, colored the same as each
 * device row's own StatusBadge. */
export function JobStatusSummary({ items }: { items: JobItem[] }) {
  if (items.length === 0) return null;

  const counts = STATUS_ORDER.map((status) => ({
    status,
    count: items.filter((i) => i.status === status).length,
  })).filter(({ count }) => count > 0);

  return (
    <div className="job-status-summary">
      <div className="job-status-bar">
        {counts.map(({ status, count }) => (
          <div
            key={status}
            className={`job-status-bar-segment status-${status}`}
            style={{ width: `${(count / items.length) * 100}%` }}
            title={`${STATUS_LABELS[status]}: ${count}`}
          />
        ))}
      </div>
      <div className="job-status-legend">
        {counts.map(({ status, count }) => (
          <span key={status} className="job-status-legend-item">
            <span className={`job-status-dot status-${status}`} />
            {STATUS_LABELS[status]} {count}
          </span>
        ))}
      </div>
    </div>
  );
}
