import { apiClient } from "./client";

export type LogKind = "collection" | "firmware" | "audit" | "other";

export interface LogEntry {
  path: string;
  day: string;
  kind: LogKind;
  name: string;
  size_bytes: number;
  modified_at: string;
}

export interface LogHistory {
  directory: string;
  retention_days: number;
  total_files: number;
  total_bytes: number;
  files: LogEntry[];
}

export interface LogFile {
  path: string;
  size_bytes: number;
  truncated: boolean;
  content: string;
}

export const logHistoryApi = {
  list: (params: { kind?: string; q?: string }) => apiClient.get<LogHistory>("/api/log-history", { params }),
  read: (path: string) => apiClient.get<LogFile>("/api/log-history/file", { params: { path } }),
  download: (path: string) => apiClient.get<Blob>("/api/log-history/download", { params: { path }, responseType: "blob" }),
  downloadZip: (paths: string[]) => apiClient.post<Blob>("/api/log-history/download.zip", { paths }, { responseType: "blob" }),
  remove: (paths: string[]) => apiClient.post<{ deleted: number }>("/api/log-history/delete", { paths }),
  purge: (olderThanDays: number) => apiClient.post<{ deleted: number }>("/api/log-history/purge", { older_than_days: olderThanDays }),
};
