import { useMemo } from "react";

import type { FirmwareJobItem, FirmwareJobStatus } from "../api/types";

const STATUS_ORDER: FirmwareJobStatus[] = [
  "pending",
  "authenticating",
  "transferring",
  "verifying",
  "reloading",
  "completed",
  "failed",
  "cancelled",
];
const STATUS_LABELS: Record<FirmwareJobStatus, string> = {
  pending: "Pending",
  running: "Running",
  authenticating: "Authenticating",
  transferring: "Transferring",
  verifying: "Verifying",
  reloading: "Reloading",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

/** Same segmented-progress-bar-plus-legend as JobStatusSummary, adapted for
 * a firmware push's more granular per-device phases (transferring/
 * verifying/reloading instead of a single "running"). */
export function FirmwareJobStatusSummary({ items }: { items: FirmwareJobItem[] }) {
  const counts = useMemo(() => {
    const byStatus = new Map<FirmwareJobStatus, number>();
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
