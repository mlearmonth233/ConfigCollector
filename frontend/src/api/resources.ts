import { apiClient } from "./client";
import type {
  Credential,
  CurrentUser,
  Device,
  DeviceImportResult,
  DeviceType,
  Job,
  JobDetail,
  MfaMode,
  Snapshot,
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
  remove: (id: string) => apiClient.delete(`/api/credentials/${id}`),
};

export const deviceTypesApi = {
  list: () => apiClient.get<DeviceType[]>("/api/device-types"),
};

export const devicesApi = {
  list: () => apiClient.get<Device[]>("/api/devices"),
  create: (data: Partial<Device>) => apiClient.post<Device>("/api/devices", data),
  update: (id: string, data: Partial<Device>) => apiClient.patch<Device>(`/api/devices/${id}`, data),
  remove: (id: string) => apiClient.delete(`/api/devices/${id}`),
  importCsv: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiClient.post<DeviceImportResult>("/api/devices/import", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
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
};

export const snapshotsApi = {
  get: (id: string) => apiClient.get<Snapshot>(`/api/snapshots/${id}`),
};
