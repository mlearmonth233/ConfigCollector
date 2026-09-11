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
  // Used for any device with no credential_id of its own - at most one
  // per org.
  is_default: boolean;
  created_at: string;
}

export type NetworkZone = "it" | "ot";

export interface Device {
  id: string;
  name: string;
  host: string;
  port: number;
  device_type: string;
  site: string | null;
  credential_id: string | null;
  custom_commands: string | null;
  device_role: string | null;
  network_zone: NetworkZone | null;
  created_at: string;
}

export interface DeviceRole {
  key: string;
  label: string;
}

export interface DeviceDetection {
  device_role: string | null;
  device_role_label: string | null;
  network_zone: NetworkZone | null;
  suggested_device_type: string | null;
}

export interface DeviceType {
  key: string;
  label: string;
  category: string;
  requires_custom_command: boolean;
  default_commands: string[];
  is_custom_default: boolean;
}

export interface CommandProfile {
  device_type: string;
  label: string;
  category: string;
  commands: string[];
  is_custom: boolean;
}

export interface DeviceImportResult {
  created: number;
  errors: string[];
}

export type JobStatus = "pending" | "authenticating" | "running" | "completed" | "failed" | "cancelled";

export interface JobItem {
  id: string;
  // Null if the device was later deleted - the item's history (status,
  // error, live output, any snapshot) is kept either way.
  device_id: string | null;
  device_name: string;
  status: JobStatus;
  error_message: string | null;
  used_fallback_credential: boolean;
  live_output: string;
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

export interface NeighborGap {
  name: string;
  ip: string | null;
  protocols: string[];
  seen_from: string[];
}

export interface NeighborGapCheck {
  checked_item_count: number;
  missing: NeighborGap[];
}

export interface Snapshot {
  id: string;
  device_id: string | null;
  collected_at: string;
  content: string;
}

export interface SnapshotSummary {
  id: string;
  device_id: string | null;
  collected_at: string;
}

export interface SnapshotDiff {
  from_id: string;
  to_id: string;
  from_collected_at: string;
  to_collected_at: string;
  diff: string[];
}

export type ScheduleFrequency = "every_n_hours" | "daily";

export interface Schedule {
  id: string;
  name: string;
  enabled: boolean;
  device_ids: string[] | null; // null = every device in the org
  frequency: ScheduleFrequency;
  interval_hours: number | null;
  run_at_hour: number | null;
  run_at_minute: number | null;
  next_run_at: string;
  last_run_at: string | null;
  last_job_id: string | null;
  created_at: string;
}

export interface OrganizationSettings {
  name: string;
  snapshot_retention_days: number | null;
}
