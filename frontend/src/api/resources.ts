import { apiClient } from "./client";
import type {
  CommandProfile,
  Credential,
  CurrentUser,
  Device,
  DeviceDetection,
  DeviceImportResult,
  DeviceRole,
  DeviceType,
  Job,
  JobDetail,
  MfaMode,
  NeighborGapCheck,
  OrganizationSettings,
  Schedule,
  ScheduleFrequency,
  Snapshot,
  SnapshotDiff,
  SnapshotSummary,
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
  importCsv: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiClient.post<DeviceImportResult>("/api/devices/import", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  downloadImportTemplate: () =>
    apiClient.get("/api/devices/import-template", { responseType: "blob" }),
  listSnapshots: (deviceId: string) =>
    apiClient.get<SnapshotSummary[]>(`/api/devices/${deviceId}/snapshots`),
};

export interface JobCreatePayload {
  deviceIds?: string[];
  commandsByDeviceType?: Record<string, string>;
  credentialOtps?: Record<string, string>;
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
  remove: (id: string) => apiClient.delete(`/api/jobs/${id}`),
  clearFinished: () => apiClient.delete<{ deleted: number }>("/api/jobs"),
  checkNeighborGaps: (id: string) => apiClient.get<NeighborGapCheck>(`/api/jobs/${id}/neighbor-gaps`),
};

export const snapshotsApi = {
  get: (id: string) => apiClient.get<Snapshot>(`/api/snapshots/${id}`),
  diff: (fromId: string, toId: string) =>
    apiClient.get<SnapshotDiff>("/api/snapshots/diff", { params: { from_id: fromId, to_id: toId } }),
};

export interface ScheduleCreatePayload {
  name: string;
  device_ids?: string[];
  frequency: ScheduleFrequency;
  interval_hours?: number;
  run_at_hour?: number;
  run_at_minute?: number;
}

export interface ScheduleUpdatePayload {
  name?: string;
  enabled?: boolean;
  device_ids?: string[];
  clear_device_ids?: boolean;
  frequency?: ScheduleFrequency;
  interval_hours?: number;
  run_at_hour?: number;
  run_at_minute?: number;
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
