import type { JobStatus } from "../api/types";

export function StatusBadge({ status }: { status: JobStatus }) {
  return <span className={`status-badge status-${status}`}>{status}</span>;
}
