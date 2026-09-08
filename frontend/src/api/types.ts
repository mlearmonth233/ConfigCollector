export interface CurrentUser {
  id: string;
  org_id: string;
  org_name: string;
  email: string;
  role: "admin" | "member";
}

export interface Credential {
  id: string;
  name: string;
  username: string;
  has_enable_secret: boolean;
  created_at: string;
}

export interface Device {
  id: string;
  name: string;
  host: string;
  port: number;
  device_type: string;
  site: string | null;
  credential_id: string | null;
  custom_commands: string | null;
  created_at: string;
}

export interface DeviceType {
  key: string;
  label: string;
  category: string;
  requires_custom_command: boolean;
}

export interface DeviceImportResult {
  created: number;
  errors: string[];
}

export type JobStatus = "pending" | "running" | "completed" | "failed";

export interface JobItem {
  id: string;
  device_id: string;
  device_name: string;
  status: JobStatus;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  snapshot_id: string | null;
}

export interface Job {
  id: string;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  item_count: number;
}

export interface JobDetail extends Job {
  items: JobItem[];
}

export interface Snapshot {
  id: string;
  device_id: string;
  collected_at: string;
  content: string;
}

export interface SnapshotSummary {
  id: string;
  device_id: string;
  collected_at: string;
}
