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
  snmp_profile_id: string | null;
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
  custom: boolean; // an org-defined type (see CustomDeviceType) rather than a built-in one
}

export interface CustomDeviceType {
  id: string;
  key: string;
  label: string;
  category: string;
  netmiko_driver: string;
  default_commands: string[];
  secret_supported: boolean;
  timing_read: boolean;
  device_count: number;
  created_at: string;
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

export interface DeviceClearResult {
  deleted: number;
  skipped: number;
}

export interface DeviceReachability {
  device_id: string;
  host: string;
  ping_ok: boolean;
  dns_ok: boolean;
  resolved_ip: string | null;
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

export type ScheduleFrequency = "once" | "every_n_hours" | "daily" | "weekly" | "monthly";

export interface Schedule {
  id: string;
  name: string;
  enabled: boolean;
  device_ids: string[] | null; // null = every device in the org
  frequency: ScheduleFrequency;
  interval_hours: number | null; // every_n_hours
  run_at_hour: number | null; // daily / weekly / monthly, in `timezone`
  run_at_minute: number | null;
  day_of_week: number | null; // weekly: 0 = Monday ... 6 = Sunday
  day_of_month: number | null; // monthly: 1..31 (clamped to shorter months)
  run_once_at: string | null; // once
  timezone: string | null; // IANA name; null = UTC
  next_run_at: string;
  last_run_at: string | null;
  last_job_id: string | null;
  created_at: string;
}

export interface OrganizationSettings {
  name: string;
  snapshot_retention_days: number | null;
}

export type TransferProtocol = "tftp" | "ftp" | "scp";

export interface FirmwareImage {
  id: string;
  original_filename: string;
  label: string | null;
  size_bytes: number;
  created_at: string;
}

export interface NetworkInterface {
  name: string;
  address: string;
}

export interface FirmwareJobItem {
  id: string;
  device_id: string | null;
  device_name: string;
  status: JobStatus;
  error_message: string | null;
  live_output: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface FirmwareJob {
  id: string;
  firmware_image_id: string | null;
  protocol: TransferProtocol;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  item_count: number;
}

export interface FirmwareJobDetail extends FirmwareJob {
  items: FirmwareJobItem[];
}

export interface DnsCheckJobItem {
  id: string;
  target: string;
  status: JobStatus;
  ping_ok: boolean | null;
  forward_ok: boolean | null;
  forward_ips: string[];
  reverse_ok: boolean | null;
  reverse_hostname: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface DnsCheckJob {
  id: string;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  item_count: number;
}

export interface DnsCheckJobDetail extends DnsCheckJob {
  items: DnsCheckJobItem[];
}

export type SnmpVersion = "v2c" | "v3";
export type SnmpSecurityLevel = "noAuthNoPriv" | "authNoPriv" | "authPriv";
export type SnmpAuthProtocol = "MD5" | "SHA" | "SHA224" | "SHA256" | "SHA384" | "SHA512";
export type SnmpPrivProtocol = "DES" | "3DES" | "AES128" | "AES192" | "AES256";

export interface SnmpProfile {
  id: string;
  name: string;
  is_default: boolean;
  version: SnmpVersion;
  port: number;
  timeout_seconds: number;
  retries: number;
  has_community: boolean;
  username: string | null;
  security_level: SnmpSecurityLevel | null;
  auth_protocol: SnmpAuthProtocol | null;
  has_auth_password: boolean;
  priv_protocol: SnmpPrivProtocol | null;
  has_priv_password: boolean;
  context_name: string | null;
  device_count: number;
  created_at: string;
}

export interface SnmpJobItem {
  id: string;
  device_id: string | null;
  device_name: string;
  device_host: string | null;
  profile_name: string | null;
  status: JobStatus;
  error_message: string | null;
  live_output: string;
  report: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface SnmpJob {
  id: string;
  status: JobStatus;
  snmp_profile_id: string | null;
  extra_oids: string[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  item_count: number;
}

export interface SnmpJobDetail extends SnmpJob {
  items: SnmpJobItem[];
}

export type HostnameMatchMode = "contains" | "prefix" | "suffix" | "regex";

export interface HostnameRule {
  pattern: string;
  match_mode: HostnameMatchMode;
  device_role: string | null;
  role_label: string | null;
  network_zone: NetworkZone | null;
  device_type: string | null;
}

export interface HostnameRules {
  rules: (HostnameRule & { sort_order: number })[];
  using_builtin: boolean;
}

export type SnmpAlertKind = "link_down" | "link_up" | "ap_down" | "ap_up" | "device_down" | "device_up" | "syslog";

export interface SnmpMonitorConfig {
  enabled: boolean;
  interval_minutes: number;
  device_ids: string[] | null;
  snmp_profile_id: string | null;
  alert_link_down: boolean;
  alert_link_up: boolean;
  alert_ap_down: boolean;
  alert_ap_up: boolean;
  alert_device_down: boolean;
  alert_device_up: boolean;
  alert_syslog_max_level: number | null;
  recipients: string[];
  smtp_host: string | null;
  smtp_port: number;
  smtp_username: string | null;
  has_smtp_password: boolean;
  smtp_starttls: boolean;
  smtp_ssl: boolean;
  smtp_from: string | null;
  next_run_at: string | null;
  last_run_at: string | null;
  last_result: string | null;
  monitored_device_count: number;
}

export interface SnmpAlert {
  id: string;
  device_id: string | null;
  device_name: string;
  kind: SnmpAlertKind;
  kind_label: string;
  subject: string;
  detail: string | null;
  emailed: boolean;
  email_error: string | null;
  created_at: string;
}

export interface LogFile {
  name: string;
  size_bytes: number;
  modified_at: string;
}

export interface LogOverview {
  enabled: boolean;
  directory: string;
  level: string;
  files: LogFile[];
}

export interface LogTail {
  name: string;
  lines: number;
  content: string;
}
