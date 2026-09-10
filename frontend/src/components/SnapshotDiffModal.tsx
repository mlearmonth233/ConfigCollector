import { useEffect, useState } from "react";

import { extractErrorMessage } from "../api/client";
import { snapshotsApi } from "../api/resources";
import type { SnapshotDiff } from "../api/types";

function lineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "diff-line diff-line-file";
  if (line.startsWith("+")) return "diff-line diff-line-add";
  if (line.startsWith("-")) return "diff-line diff-line-remove";
  if (line.startsWith("@@")) return "diff-line diff-line-hunk";
  return "diff-line";
}

interface Props {
  fromId: string;
  toId: string;
  onClose: () => void;
}

/** Unified diff between two snapshots of the same device - what changed in
 * its config between two collection runs. */
export function SnapshotDiffModal({ fromId, toId, onClose }: Props) {
  const [diff, setDiff] = useState<SnapshotDiff | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    snapshotsApi
      .diff(fromId, toId)
      .then(({ data }) => {
        if (!cancelled) setDiff(data);
      })
      .catch((err) => {
        if (!cancelled) setError(extractErrorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [fromId, toId]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" style={{ width: "min(900px, 95vw)" }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Config diff</h2>
          <div className="modal-header-actions">
            <button onClick={onClose}>Close</button>
          </div>
        </div>
        {error && <div className="error-banner" style={{ margin: 18 }}>{error}</div>}
        {!diff && !error && <p style={{ padding: 18 }}>Loading…</p>}
        {diff && (
          <>
            <p className="page-subtitle" style={{ margin: "12px 18px 0" }}>
              {new Date(diff.from_collected_at).toLocaleString()} → {new Date(diff.to_collected_at).toLocaleString()}
            </p>
            {diff.diff.length === 0 ? (
              <p className="field-hint" style={{ padding: "0 18px 18px" }}>
                No differences - these two collections are identical.
              </p>
            ) : (
              <pre className="config-view diff-view">
                {diff.diff.map((line, i) => (
                  <div key={i} className={lineClass(line)}>
                    {line}
                  </div>
                ))}
              </pre>
            )}
          </>
        )}
      </div>
    </div>
  );
}
