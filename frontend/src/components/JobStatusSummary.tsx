import { useMemo } from "react";

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
  // One pass building a count per status, rather than one filter() pass per
  // entry in STATUS_ORDER - this reruns on every poll tick while a job is
  // active, so it's worth not scanning the item list six times over.
  const counts = useMemo(() => {
    const byStatus = new Map<JobStatus, number>();
    for (const item of items) {
      byStatus.set(item.status, (byStatus.get(item.status) ?? 0) + 1);
    }
    return STATUS_ORDER.map((status) => ({ status, count: byStatus.get(status) ?? 0 })).filter(
      ({ count }) => count > 0
    );
  }, [items]);

  if (items.length === 0) return null;

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
