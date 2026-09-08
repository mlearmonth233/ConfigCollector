import { useState } from "react";

export type FileExtension = "txt" | "log";

export interface DownloadPrefs {
  extension: FileExtension;
  includeTimestamp: boolean;
}

const PREFS_KEY = "configcollector_download_prefs";
const DEFAULT_PREFS: DownloadPrefs = { extension: "txt", includeTimestamp: false };

function loadPrefs(): DownloadPrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return DEFAULT_PREFS;
    const parsed = JSON.parse(raw);
    return {
      extension: parsed.extension === "log" ? "log" : "txt",
      includeTimestamp: Boolean(parsed.includeTimestamp),
    };
  } catch {
    return DEFAULT_PREFS;
  }
}

function savePrefs(prefs: DownloadPrefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    // best-effort only - a private window or full storage just means the
    // choice won't be remembered next time
  }
}

/** Shared, browser-persisted file type / timestamp download preferences -
 * used by both the single-snapshot and job-wide bulk downloads, so picking
 * one in either place carries over to the other. */
export function useDownloadPrefs() {
  const [prefs, setPrefs] = useState<DownloadPrefs>(loadPrefs);

  function updatePrefs(next: Partial<DownloadPrefs>) {
    setPrefs((prev) => {
      const merged = { ...prev, ...next };
      savePrefs(merged);
      return merged;
    });
  }

  return { prefs, updatePrefs };
}
