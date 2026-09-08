import { useEffect, useState } from "react";

import { apiClient, extractErrorMessage } from "../api/client";
import { snapshotsApi } from "../api/resources";

export function SnapshotModal({ snapshotId, onClose }: { snapshotId: string; onClose: () => void }) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The download endpoint requires a Bearer token, which a plain <a href>
  // navigation can't send - so fetch it via the authenticated axios client
  // and save the response as a blob instead.
  async function handleDownload() {
    try {
      const response = await apiClient.get(`/api/snapshots/${snapshotId}/download`, {
        responseType: "blob",
      });
      const disposition: string = response.headers["content-disposition"] ?? "";
      const match = /filename="?([^"]+)"?/.exec(disposition);
      const filename = match?.[1] ?? `${snapshotId}.txt`;

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
            <button onClick={handleDownload}>Download</button>
            <button onClick={onClose}>Close</button>
          </div>
        </div>
        {error && <div className="error-banner">{error}</div>}
        {content === null && !error ? <p>Loading…</p> : <pre className="config-view">{content}</pre>}
      </div>
    </div>
  );
}
