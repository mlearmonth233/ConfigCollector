export interface CurrentUser {
  id: string;
  org_id: string;
  org_name: string;
  email: string;
  role: "admin" | "member";
}

export type MfaMode = "none" | "push" | "passcode";

export interface Credential {
  id: string;
  name: string;
  username: string;
  has_enable_secret: boolean;
  mfa_mode: MfaMode;
  otp_delimiter: string;
  auth_timeout_seconds: number;
  fallback_credential_id: string | null;
  fallback_credential_name: string | null;
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
  default_commands: string[];
}

export interface DeviceImportResult {
  created: number;
  errors: string[];
}

export type JobStatus = "pending" | "authenticating" | "running" | "completed" | "failed";

export interface JobItem {
  id: string;
  device_id: string;
  device_name: string;
  status: JobStatus;
  error_message: string | null;
  used_fallback_credential: boolean;
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
