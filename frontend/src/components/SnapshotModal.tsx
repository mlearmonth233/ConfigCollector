import { useEffect, useState } from "react";

import { apiClient, extractErrorMessage } from "../api/client";
import { saveBlobResponse } from "../api/download";
import { snapshotsApi } from "../api/resources";
import { DownloadOptions } from "./DownloadOptions";
import { useDownloadPrefs } from "../hooks/useDownloadPrefs";

export function SnapshotModal({ snapshotId, onClose }: { snapshotId: string; onClose: () => void }) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { prefs, updatePrefs } = useDownloadPrefs();

  // The filename (hostname-based, with the chosen extension/timestamp)
  // comes from the server's Content-Disposition header, so it stays a
  // single source of truth shared with any direct API caller.
  async function handleDownload() {
    try {
      const response = await apiClient.get(`/api/snapshots/${snapshotId}/download`, {
        responseType: "blob",
        params: { ext: prefs.extension, include_timestamp: prefs.includeTimestamp },
      });
      saveBlobResponse(response, `snapshot.${prefs.extension}`);
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
          <DownloadOptions prefs={prefs} onChange={updatePrefs} />
          <button onClick={handleDownload}>Download</button>
        </div>
      </div>
    </div>
  );
}
