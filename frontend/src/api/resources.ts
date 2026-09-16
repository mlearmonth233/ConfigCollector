import { apiClient } from "./client";
import type {
  AlertSettings,
  AlertTestResult,
  CommandProfile,
  Credential,
  CurrentUser,
  CustomDeviceType,
  Device,
  DeviceClearResult,
  DeviceDetection,
  DeviceReachability,
  DeviceRole,
  DeviceType,
  DnsCheckJob,
  DnsCheckJobDetail,
  FirmwareImage,
  FirmwareJob,
  FirmwareJobDetail,
  HostnameRule,
  HostnameRules,
  Job,
  JobDetail,
  LogOverview,
  LogTail,
  MfaMode,
  NeighborGapCheck,
  NetworkInterface,
  OrganizationSettings,
  PingMonitorSettings,
  PingOverview,
  Schedule,
  ScheduleFrequency,
  Snapshot,
  SnapshotDiff,
  SnapshotSummary,
  SnmpAlert,
  SnmpJob,
  SnmpJobDetail,
  SnmpMonitorConfig,
  SnmpProfile,
  TransferProtocol,
} from "./types";

export const authApi = {
  register: (org_name: string, email: string, password: string) =>
    apiClient.post<{ access_token: string }>("/api/auth/register", { org_name, email, password }),
  login: (email: string, password: string) =>
    apiClient.post<{ access_token: string }>("/api/auth/login", { email, password }),
  me: () => apiClient.get<CurrentUser>("/api/auth/me"),
};

export interface CredentialCreatePayload {
  name: string;
  username: string;
  password: string;
  enable_secret?: string;
  mfa_mode?: MfaMode;
  otp_delimiter?: string;
  auth_timeout_seconds?: number;
  fallback_credential_id?: string;
}

export const credentialsApi = {
  list: () => apiClient.get<Credential[]>("/api/credentials"),
  create: (data: CredentialCreatePayload) => apiClient.post<Credential>("/api/credentials", data),
  setDefault: (id: string) => apiClient.post<Credential>(`/api/credentials/${id}/set-default`),
  remove: (id: string) => apiClient.delete(`/api/credentials/${id}`),
};

export const deviceTypesApi = {
  list: () => apiClient.get<DeviceType[]>("/api/device-types"),
};

export interface CustomDeviceTypePayload {
  key: string;
  label: string;
  category: string;
  netmiko_driver: string;
  default_commands: string[];
  secret_supported: boolean;
  timing_read: boolean;
}

export const customDeviceTypesApi = {
  list: () => apiClient.get<CustomDeviceType[]>("/api/custom-device-types"),
  drivers: () => apiClient.get<{ drivers: string[]; categories: string[] }>("/api/custom-device-types/netmiko-drivers"),
  create: (data: CustomDeviceTypePayload) => apiClient.post<CustomDeviceType>("/api/custom-device-types", data),
  update: (id: string, data: Partial<Omit<CustomDeviceTypePayload, "key">>) =>
    apiClient.patch<CustomDeviceType>(`/api/custom-device-types/${id}`, data),
  remove: (id: string) => apiClient.delete(`/api/custom-device-types/${id}`),
};

export const deviceRolesApi = {
  list: () => apiClient.get<DeviceRole[]>("/api/device-roles"),
};

export const commandProfilesApi = {
  list: () => apiClient.get<CommandProfile[]>("/api/command-profiles"),
  save: (deviceType: string, commands: string[]) =>
    apiClient.put<CommandProfile>(`/api/command-profiles/${deviceType}`, { commands }),
  reset: (deviceType: string) => apiClient.delete<CommandProfile>(`/api/command-profiles/${deviceType}`),
};

export const devicesApi = {
  list: () => apiClient.get<Device[]>("/api/devices"),
  create: (data: Partial<Device>) => apiClient.post<Device>("/api/devices", data),
  update: (id: string, data: Partial<Device>) => apiClient.patch<Device>(`/api/devices/${id}`, data),
  remove: (id: string) => apiClient.delete(`/api/devices/${id}`),
  detect: (name: string) => apiClient.get<DeviceDetection>("/api/devices/detect", { params: { name } }),
  listSnapshots: (deviceId: string) =>
    apiClient.get<SnapshotSummary[]>(`/api/devices/${deviceId}/snapshots`),
  checkReachability: () => apiClient.get<DeviceReachability[]>("/api/devices/reachability"),
  clearAll: () => apiClient.delete<DeviceClearResult>("/api/devices"),
};

export interface JobCreatePayload {
  deviceIds?: string[];
  commandsByDeviceType?: Record<string, string>;
  credentialOtps?: Record<string, string>;
}

export interface JobItemRetryPayload {
  commands?: string;
  credential_otp?: string;
  fallback_otp?: string;
}

export const jobsApi = {
  list: () => apiClient.get<Job[]>("/api/jobs"),
  create: ({ deviceIds, commandsByDeviceType, credentialOtps }: JobCreatePayload) =>
    apiClient.post<JobDetail>("/api/jobs", {
      device_ids: deviceIds,
      commands_by_device_type: commandsByDeviceType,
      credential_otps: credentialOtps,
    }),
  get: (id: string) => apiClient.get<JobDetail>(`/api/jobs/${id}`),
  cancel: (id: string) => apiClient.post<JobDetail>(`/api/jobs/${id}/cancel`),
  forceStop: (id: string) => apiClient.post<JobDetail>(`/api/jobs/${id}/force-stop`),
  remove: (id: string) => apiClient.delete(`/api/jobs/${id}`),
  clearFinished: () => apiClient.delete<{ deleted: number }>("/api/jobs"),
  checkNeighborGaps: (id: string) => apiClient.get<NeighborGapCheck>(`/api/jobs/${id}/neighbor-gaps`),
  retryItem: (jobId: string, itemId: string, payload: JobItemRetryPayload) =>
    apiClient.post<JobDetail>(`/api/jobs/${jobId}/items/${itemId}/retry`, payload),
};

export const snapshotsApi = {
  get: (id: string) => apiClient.get<Snapshot>(`/api/snapshots/${id}`),
  diff: (fromId: string, toId: string) =>
    apiClient.get<SnapshotDiff>("/api/snapshots/diff", { params: { from_id: fromId, to_id: toId } }),
};

export interface ScheduleTimingPayload {
  frequency: ScheduleFrequency;
  interval_hours?: number;
  run_at_hour?: number;
  run_at_minute?: number;
  day_of_week?: number;
  day_of_month?: number;
  run_once_at?: string; // ISO datetime
  timezone?: string; // IANA name the time-of-day fields are in
}

export interface ScheduleCreatePayload extends ScheduleTimingPayload {
  name: string;
  device_ids?: string[];
}

export interface ScheduleUpdatePayload extends Partial<ScheduleTimingPayload> {
  name?: string;
  enabled?: boolean;
  device_ids?: string[];
  clear_device_ids?: boolean;
}

export const schedulesApi = {
  list: () => apiClient.get<Schedule[]>("/api/schedules"),
  create: (data: ScheduleCreatePayload) => apiClient.post<Schedule>("/api/schedules", data),
  update: (id: string, data: ScheduleUpdatePayload) => apiClient.patch<Schedule>(`/api/schedules/${id}`, data),
  remove: (id: string) => apiClient.delete(`/api/schedules/${id}`),
  runNow: (id: string) => apiClient.post<JobDetail>(`/api/schedules/${id}/run-now`),
};

export const organizationApi = {
  get: () => apiClient.get<OrganizationSettings>("/api/organization"),
  update: (data: { snapshot_retention_days?: number; clear_retention?: boolean }) =>
    apiClient.patch<OrganizationSettings>("/api/organization", data),
};

export interface FirmwareJobCreatePayload {
  firmware_image_id: string;
  device_ids: string[];
  protocol: TransferProtocol;
  server_host: string;
  // Optional per type - anything left out falls back to the server's
  // built-in copy command for that device type (see getPushDefaults).
  commands_by_device_type?: Record<string, string>;
  credential_otps?: Record<string, string>;
}

export interface PushDefaults {
  commands_by_device_type: Record<string, string>;
  placeholders: string[];
}

export const dnsCheckApi = {
  createJob: (targets: string[]) => apiClient.post<DnsCheckJobDetail>("/api/dns-check/jobs", { targets }),
  listJobs: () => apiClient.get<DnsCheckJob[]>("/api/dns-check/jobs"),
  getJob: (id: string) => apiClient.get<DnsCheckJobDetail>(`/api/dns-check/jobs/${id}`),
  cancelJob: (id: string) => apiClient.post<DnsCheckJobDetail>(`/api/dns-check/jobs/${id}/cancel`),
  removeJob: (id: string) => apiClient.delete(`/api/dns-check/jobs/${id}`),
  clearFinished: () => apiClient.delete<{ deleted: number }>("/api/dns-check/jobs"),
};

export const firmwareApi = {
  listImages: () => apiClient.get<FirmwareImage[]>("/api/firmware/images"),
  uploadImage: (file: File, label?: string) => {
    const form = new FormData();
    form.append("file", file);
    return apiClient.post<FirmwareImage>("/api/firmware/images", form, {
      headers: { "Content-Type": "multipart/form-data" },
      params: label ? { label } : undefined,
    });
  },
  removeImage: (id: string) => apiClient.delete(`/api/firmware/images/${id}`),
  listNetworkInterfaces: () => apiClient.get<NetworkInterface[]>("/api/firmware/network-interfaces"),
  getPushDefaults: () => apiClient.get<PushDefaults>("/api/firmware/push-defaults"),
  createJob: (payload: FirmwareJobCreatePayload) => apiClient.post<FirmwareJobDetail>("/api/firmware/jobs", payload),
  listJobs: () => apiClient.get<FirmwareJob[]>("/api/firmware/jobs"),
  getJob: (id: string) => apiClient.get<FirmwareJobDetail>(`/api/firmware/jobs/${id}`),
  cancelJob: (id: string) => apiClient.post<FirmwareJobDetail>(`/api/firmware/jobs/${id}/cancel`),
  forceStopJob: (id: string) => apiClient.post<FirmwareJobDetail>(`/api/firmware/jobs/${id}/force-stop`),
};

export interface SnmpProfilePayload {
  name: string;
  version: "v2c" | "v3";
  port?: number;
  timeout_seconds?: number;
  retries?: number;
  community?: string;
  username?: string;
  security_level?: string;
  auth_protocol?: string;
  auth_password?: string;
  priv_protocol?: string;
  priv_password?: string;
  context_name?: string;
  is_default?: boolean;
}

export const snmpApi = {
  listProfiles: () => apiClient.get<SnmpProfile[]>("/api/snmp/profiles"),
  createProfile: (data: SnmpProfilePayload) => apiClient.post<SnmpProfile>("/api/snmp/profiles", data),
  updateProfile: (id: string, data: Partial<SnmpProfilePayload>) =>
    apiClient.patch<SnmpProfile>(`/api/snmp/profiles/${id}`, data),
  setDefaultProfile: (id: string) => apiClient.post<SnmpProfile>(`/api/snmp/profiles/${id}/set-default`),
  removeProfile: (id: string) => apiClient.delete(`/api/snmp/profiles/${id}`),
  createJob: (payload: { device_ids: string[]; snmp_profile_id?: string; extra_oids?: string[] }) =>
    apiClient.post<SnmpJobDetail>("/api/snmp/jobs", payload),
  listJobs: () => apiClient.get<SnmpJob[]>("/api/snmp/jobs"),
  getJob: (id: string) => apiClient.get<SnmpJobDetail>(`/api/snmp/jobs/${id}`),
  cancelJob: (id: string) => apiClient.post<SnmpJobDetail>(`/api/snmp/jobs/${id}/cancel`),
  removeJob: (id: string) => apiClient.delete(`/api/snmp/jobs/${id}`),
  clearFinished: () => apiClient.delete<{ deleted: number }>("/api/snmp/jobs"),
};

export const hostnameRulesApi = {
  get: () => apiClient.get<HostnameRules>("/api/hostname-rules"),
  replace: (rules: HostnameRule[]) => apiClient.put<HostnameRules>("/api/hostname-rules", { rules }),
  test: (name: string, rules?: HostnameRule[]) =>
    apiClient.post<DeviceDetection>("/api/hostname-rules/test", { name, rules }),
};

export interface SnmpMonitorConfigPayload {
  enabled: boolean;
  interval_minutes: number;
  device_ids?: string[] | null;
  snmp_profile_id?: string | null;
  alert_link_down: boolean;
  alert_link_up: boolean;
  alert_ap_down: boolean;
  alert_ap_up: boolean;
  alert_device_down: boolean;
  alert_device_up: boolean;
  alert_syslog_max_level: number | null;
}

export const snmpMonitorApi = {
  get: () => apiClient.get<SnmpMonitorConfig>("/api/snmp/monitor"),
  update: (data: SnmpMonitorConfigPayload) => apiClient.put<SnmpMonitorConfig>("/api/snmp/monitor", data),
  runNow: () => apiClient.post<SnmpMonitorConfig>("/api/snmp/monitor/run-now"),
};

export interface AlertSettingsPayload {
  recipients: string[];
  smtp_host?: string | null;
  smtp_port: number;
  smtp_username?: string | null;
  /** Write-only: omitted/blank keeps the saved password. */
  smtp_password?: string;
  clear_smtp_password?: boolean;
  smtp_starttls: boolean;
  smtp_ssl: boolean;
  smtp_from?: string | null;
  /** Blank keeps the saved URL, "-" clears it, anything else replaces it. */
  teams_webhook_url?: string | null;
  slack_webhook_url?: string | null;
  alert_config_change: boolean;
}

export const alertsApi = {
  settings: () => apiClient.get<AlertSettings>("/api/alerts/settings"),
  updateSettings: (data: AlertSettingsPayload) => apiClient.put<AlertSettings>("/api/alerts/settings", data),
  test: () => apiClient.post<AlertTestResult>("/api/alerts/test"),
  list: () => apiClient.get<SnmpAlert[]>("/api/alerts"),
  clear: () => apiClient.delete<{ deleted: number }>("/api/alerts"),
};

export const logsApi = {
  overview: () => apiClient.get<LogOverview>("/api/logs"),
  tail: (name: string, lines: number) => apiClient.get<LogTail>("/api/logs/tail", { params: { name, lines } }),
  download: () => apiClient.get<Blob>("/api/logs/download", { responseType: "blob" }),
  reportClientError: (payload: { message: string; source?: string; stack?: string; kind?: string }) =>
    apiClient.post("/api/logs/client", payload),
};

export type PingMonitorSettingsPayload = Pick<
  PingMonitorSettings,
  "enabled" | "interval_seconds" | "failure_threshold" | "timeout_ms" | "alert_on_down" | "alert_on_up" | "history_days"
>;

export const pingApi = {
  overview: () => apiClient.get<PingOverview>("/api/ping/overview"),
  updateSettings: (data: PingMonitorSettingsPayload) => apiClient.put<PingMonitorSettings>("/api/ping/settings", data),
  runNow: () => apiClient.post<PingOverview>("/api/ping/run-now"),
  history: (deviceId: string, hours: number) =>
    apiClient.get<{ device_id: string; hours: number; samples: PingOverview["devices"][number]["recent"] }>(`/api/ping/history/${deviceId}`, { params: { hours } }),
};
