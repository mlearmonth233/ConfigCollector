import { useEffect, useState } from "react";

import { apiClient, extractErrorMessage } from "../api/client";
import { snapshotsApi } from "../api/resources";

type FileExtension = "txt" | "log";

const PREFS_KEY = "configcollector_download_prefs";

interface DownloadPrefs {
  extension: FileExtension;
  includeTimestamp: boolean;
}

function loadPrefs(): DownloadPrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return { extension: "txt", includeTimestamp: false };
    const parsed = JSON.parse(raw);
    return {
      extension: parsed.extension === "log" ? "log" : "txt",
      includeTimestamp: Boolean(parsed.includeTimestamp),
    };
  } catch {
    return { extension: "txt", includeTimestamp: false };
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

export function SnapshotModal({ snapshotId, onClose }: { snapshotId: string; onClose: () => void }) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [prefs, setPrefs] = useState<DownloadPrefs>(loadPrefs);

  function updatePrefs(next: Partial<DownloadPrefs>) {
    setPrefs((prev) => {
      const merged = { ...prev, ...next };
      savePrefs(merged);
      return merged;
    });
  }

  // The download endpoint requires a Bearer token, which a plain <a href>
  // navigation can't send - so fetch it via the authenticated axios client
  // and save the response as a blob instead. The filename (hostname-based,
  // with the chosen extension/timestamp) comes from the server's
  // Content-Disposition header, so it stays a single source of truth
  // shared with any direct API caller.
  async function handleDownload() {
    try {
      const response = await apiClient.get(`/api/snapshots/${snapshotId}/download`, {
        responseType: "blob",
        params: { ext: prefs.extension, include_timestamp: prefs.includeTimestamp },
      });
      const disposition: string = response.headers["content-disposition"] ?? "";
      const match = /filename="?([^"]+)"?/.exec(disposition);
      const filename = match?.[1] ?? `snapshot.${prefs.extension}`;

      const url = window.URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useEffect(() => {
    let cancelled = false;
    snapshotsApi
      .get(snapshotId)
      .then(({ data }) => {
        if (!cancelled) setContent(data.content);
      })
      .catch((err) => {
        if (!cancelled) setError(extractErrorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [snapshotId]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Collected configuration</h2>
          <div className="modal-header-actions">
            <button onClick={onClose}>Close</button>
          </div>
        </div>
        {error && <div className="error-banner">{error}</div>}
        {content === null && !error ? <p>Loading…</p> : <pre className="config-view">{content}</pre>}
        <div className="modal-footer download-footer">
          <div className="download-options">
            <label className="inline-label">
              File type
              <select
                value={prefs.extension}
                onChange={(e) => updatePrefs({ extension: e.target.value as FileExtension })}
              >
                <option value="txt">.txt</option>
                <option value="log">.log</option>
              </select>
            </label>
            <label className="inline-label checkbox-label">
              <input
                type="checkbox"
                checked={prefs.includeTimestamp}
                onChange={(e) => updatePrefs({ includeTimestamp: e.target.checked })}
              />
              Add timestamp to filename
            </label>
          </div>
          <button onClick={handleDownload}>Download</button>
        </div>
      </div>
    </div>
  );
}
