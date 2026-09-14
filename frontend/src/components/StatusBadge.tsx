import type { FirmwareJobStatus, JobStatus } from "../api/types";

export function StatusBadge({ status }: { status: JobStatus | FirmwareJobStatus }) {
  return <span className={`status-badge status-${status}`}>{status}</span>;
}
