import { apiClient } from "./client";
import type { CommandProfile } from "./types";

export type Severity = "fail" | "warn" | "info";
export type CheckStatus = "pass" | "warn" | "fail" | "info" | "skipped";
export type R2OStatus = "pass" | "fail" | "warn" | "manual" | "no_data" | "na";

export interface SkippedLine {
  row: number;
  text: string;
  reason: string;
}

export interface TemplateProfile {
  name: string;
  platform: "9800" | "aireos";
  kind: "flex" | "local";
  template: string;
  source: string | null;
  imported_at: string | null;
  rules: number;
  optional: number;
  skipped_count: number;
  skipped: SkippedLine[];
}

export interface ImportRecord {
  filename: string;
  sha256: string;
  imported_at: string;
  imported_by: string | null;
  sheets: string[];
  rules: number;
}

export interface R2OItem {
  item_id: string;
  row: number;
  description: string;
  commands: string[];
  expected: string;
  criteria: string;
  stage: string | null;
  level: string | null;
  comments: string | null;
  enabled: boolean;
}

export interface R2OChecklist {
  name: string;
  platform: "9800" | "aireos";
  kind: "flex" | "local";
  source: string | null;
  imported_at: string | null;
  items: R2OItem[];
}

/** Mirrors backend/app/schemas/wireless_audit.py WirelessStandard. */
export interface WirelessStandard {
  ap_name_pattern: string;
  ap_name_case: "any" | "upper" | "lower";
  dns_domain: string;
  check_dns: boolean;
  check_reverse_dns: boolean;
  ap_subnets: string[];
  approved_ap_models: string[];
  country_codes: string[];
  min_ap_uplink_mbps: number;
  require_full_duplex: boolean;
  require_primary_controller: boolean;
  require_non_default_tags: boolean;
  ap_group_name_pattern: string;
  flag_default_ap_group: boolean;
  flag_mixed_ap_generations: boolean;
  legacy_ap_models: string[];
  max_aps_per_flex_site_tag: number;
  wncd_imbalance_ratio: number;
  max_wncd_cpu_percent: number;
  flag_disabled_radios: boolean;
  allowed_24ghz_channels: number[];
  approved_9800_versions: string[];
  approved_aireos_versions: string[];
  ntp_servers: string[];
  syslog_servers: string[];
  dns_servers: string[];
  require_ha: boolean;
  max_cpu_percent: number;
  max_snapshot_age_hours: number;
  security_checks: boolean;
  required_ssids: string[];
  flag_unexpected_ssids: boolean;
  open_ssids_allowed: string[];
  template_profiles: TemplateProfile[];
  profile_assignments: Record<string, string>;
  import_history: ImportRecord[];
  r2o_checklists: R2OChecklist[];
  r2o_assignments: Record<string, string>;
  r2o_import_history: ImportRecord[];
  template_9800: string;
  template_aireos: string;
  template_variables: Record<string, string>;
  client_rssi_warn: number;
  client_rssi_fail: number;
  client_snr_warn: number;
  client_snr_fail: number;
  max_clients_per_ap: number;
  flag_legacy_clients: boolean;
  log_ignore_patterns: string[];
}

export interface WirelessStandardResponse {
  standard: WirelessStandard;
  updated_at: string | null;
  is_default: boolean;
}

export interface AuditCheck {
  check_id: string;
  title: string;
  category: string;
  status: CheckStatus;
  evaluated: number;
  issues: number;
  note: string | null;
}

export interface AuditFinding {
  check_id: string;
  category: string;
  severity: Severity;
  controller_id: string;
  controller_name: string;
  subject: string | null;
  message: string;
  expected: string | null;
  actual: string | null;
}

export interface ControllerAudit {
  device_id: string;
  name: string;
  host: string;
  platform: "9800" | "aireos";
  site: string | null;
  model: string | null;
  software: string | null;
  collected_at: string | null;
  snapshot_id: string | null;
  ap_count: number;
  client_count: number;
  score: number | null;
  counts: Record<string, number>;
  checks: AuditCheck[];
  missing_commands: string[];
  template_profile: string | null;
  r2o_checklist: string | null;
  r2o_counts: Record<string, number>;
}

export interface ApAudit {
  controller_id: string;
  controller_name: string;
  name: string;
  model: string | null;
  mac: string | null;
  ip: string | null;
  serial: string | null;
  software: string | null;
  state: string | null;
  country: string | null;
  clients: number | null;
  ap_group: string | null;
  site_tag: string | null;
  policy_tag: string | null;
  rf_tag: string | null;
  primary_controller: string | null;
  uplink: string | null;
  radios: string | null;
  fqdn: string | null;
  dns_ips: string[];
  ptr: string | null;
  name_ok: boolean | null;
  subnet_ok: boolean | null;
  dns_ok: boolean | null;
  ptr_ok: boolean | null;
  issues: number;
}

export interface ClientAudit {
  controller_id: string;
  controller_name: string;
  mac: string;
  manufacturer: string | null;
  ip: string | null;
  username: string | null;
  ap_name: string | null;
  ssid: string | null;
  state: string | null;
  protocol: string | null;
  rssi: number | null;
  snr: number | null;
  health: "good" | "fair" | "poor" | "unknown";
  issues: string[];
}

export interface ClientHealth {
  controller_id: string;
  controller_name: string;
  total: number;
  with_rf_detail: number;
  good: number;
  fair: number;
  poor: number;
  avg_rssi: number | null;
  avg_snr: number | null;
  band_24: number;
  band_5: number;
  band_6: number;
  not_run: number;
  no_ip: number;
  legacy: number;
}

export interface LogGroup {
  controller_id: string;
  controller_name: string;
  source: "syslog" | "traplog";
  key: string;
  severity: number | null;
  count: number;
  example: string;
  notable: boolean;
}

export interface GroupAudit {
  controller_id: string;
  controller_name: string;
  kind: string;
  name: string;
  description: string | null;
  ap_count: number;
  models: string | null;
  wlans: string | null;
  profiles: string | null;
  wncd: number | null;
  issues: number;
}

export interface WncdRow {
  controller_id: string;
  controller_name: string;
  instance: number;
  site_tags: string[];
  aps: number;
  cpu_5min: number | null;
}

export interface R2OResult {
  controller_id: string;
  controller_name: string;
  checklist: string;
  item_id: string;
  row: number;
  description: string;
  commands: string[];
  stage: string | null;
  level: string | null;
  status: R2OStatus;
  details: string[];
  output: string;
  fix: string[];
}

export interface WirelessAuditReport {
  generated_at: string;
  dns_checked: boolean;
  summary: Record<string, number>;
  controllers: ControllerAudit[];
  findings: AuditFinding[];
  access_points: ApAudit[];
  clients: ClientAudit[];
  client_health: ClientHealth[];
  logs: LogGroup[];
  groups: GroupAudit[];
  wncd: WncdRow[];
  r2o: R2OResult[];
}

export interface CommandCoverage {
  device_type: string;
  label: string;
  device_count: number;
  audit_commands: string[];
  missing: string[];
  is_custom_profile: boolean;
}

export interface WorkbookImport {
  filename: string;
  sha256: string;
  profiles: TemplateProfile[];
  replaced: string[];
  standard: WirelessStandardResponse;
}

export interface R2OImport {
  filename: string;
  sha256: string;
  checklists: R2OChecklist[];
  replaced: string[];
}

function upload<T>(url: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return apiClient.post<T>(url, form, { headers: { "Content-Type": "multipart/form-data" } });
}

export const wirelessAuditApi = {
  run: (dns: boolean) => apiClient.get<WirelessAuditReport>("/api/wireless-audit", { params: { dns } }),
  exportXlsx: (dns: boolean) => apiClient.get<Blob>("/api/wireless-audit/export.xlsx", { params: { dns }, responseType: "blob" }),
  getStandard: () => apiClient.get<WirelessStandardResponse>("/api/wireless-audit/standard"),
  saveStandard: (standard: WirelessStandard) => apiClient.put<WirelessStandardResponse>("/api/wireless-audit/standard", standard),
  importStandard: (file: File) => upload<WorkbookImport>("/api/wireless-audit/standard/import", file),
  importR2O: (file: File) => upload<R2OImport>("/api/wireless-audit/r2o/import", file),
  coverage: () => apiClient.get<CommandCoverage[]>("/api/wireless-audit/commands"),
  addCommands: (deviceType: string) => apiClient.post<CommandProfile>(`/api/wireless-audit/commands/${deviceType}`),
  r2oCoverage: () => apiClient.get<CommandCoverage[]>("/api/wireless-audit/r2o/commands"),
  addR2OCommands: (deviceType: string) => apiClient.post<CommandProfile>(`/api/wireless-audit/r2o/commands/${deviceType}`),
};
