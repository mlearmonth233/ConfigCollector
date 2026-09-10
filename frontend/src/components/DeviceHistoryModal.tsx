import { useEffect, useState } from "react";

import { extractErrorMessage } from "../api/client";
import { devicesApi } from "../api/resources";
import type { Device, SnapshotSummary } from "../api/types";
import { SnapshotDiffModal } from "./SnapshotDiffModal";
import { SnapshotModal } from "./SnapshotModal";

interface Props {
  device: Device;
  onClose: () => void;
}

/** A device's past collections, oldest to newest - pick any two to see what
 * changed between them (config diffing), or view/download one on its own. */
export function DeviceHistoryModal({ device, onClose }: Props) {
  const [snapshots, setSnapshots] = useState<SnapshotSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [viewingSnapshotId, setViewingSnapshotId] = useState<string | null>(null);
  const [comparing, setComparing] = useState<{ from: string; to: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    devicesApi
      .listSnapshots(device.id)
      .then(({ data }) => {
        if (!cancelled) setSnapshots(data);
      })
      .catch((err) => {
        if (!cancelled) setError(extractErrorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [device.id]);

  function toggleSelected(id: string) {
    setSelected((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= 2) return [prev[1], id];
      return [...prev, id];
    });
  }

  function handleCompare() {
    if (selected.length !== 2) return;
    // Snapshots are listed newest-first - compare older -> newer regardless
    // of click order, so the diff reads as "what changed since then".
    const [a, b] = selected;
    const aIndex = snapshots?.findIndex((s) => s.id === a) ?? -1;
    const bIndex = snapshots?.findIndex((s) => s.id === b) ?? -1;
    const [olderId, newerId] = aIndex > bIndex ? [a, b] : [b, a];
    setComparing({ from: olderId, to: newerId });
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>History: {device.name}</h2>
          <div className="modal-header-actions">
            <button onClick={onClose}>Close</button>
          </div>
        </div>
        <div className="modal-body">
          {error && <div className="error-banner">{error}</div>}
          <p className="page-subtitle" style={{ marginTop: 0 }}>
            Select any two collections to see what changed between them.
          </p>

          {snapshots === null ? (
            <p>Loading…</p>
          ) : snapshots.length === 0 ? (
            <p className="empty-state">No configs collected yet for this device.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th></th>
                  <th>Collected at</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {snapshots.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selected.includes(s.id)}
                        onChange={() => toggleSelected(s.id)}
                      />
                    </td>
                    <td>{new Date(s.collected_at).toLocaleString()}</td>
                    <td>
                      <button className="link-button" onClick={() => setViewingSnapshotId(s.id)}>
                        View
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="modal-footer">
          <p className="page-subtitle" style={{ margin: 0, marginRight: "auto" }}>
            {selected.length === 2 ? "2 selected" : `Select ${2 - selected.length} more to compare`}
          </p>
          <button onClick={handleCompare} disabled={selected.length !== 2}>
            Compare selected
          </button>
        </div>
      </div>

      {viewingSnapshotId && (
        <SnapshotModal snapshotId={viewingSnapshotId} onClose={() => setViewingSnapshotId(null)} />
      )}
      {comparing && (
        <SnapshotDiffModal fromId={comparing.from} toId={comparing.to} onClose={() => setComparing(null)} />
      )}
    </div>
  );
}
