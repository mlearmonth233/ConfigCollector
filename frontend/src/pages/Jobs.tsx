import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { credentialsApi, devicesApi, deviceTypesApi, jobsApi } from "../api/resources";
import type { Credential, Device, DeviceType, Job } from "../api/types";
import { StartCollectionModal } from "../components/StartCollectionModal";
import { StatusBadge } from "../components/StatusBadge";

export function Jobs() {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceTypes, setDeviceTypes] = useState<DeviceType[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [collectionTarget, setCollectionTarget] = useState<Device[] | null>(null);
  const [rerunning, setRerunning] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadJobs() {
      try {
        const { data } = await jobsApi.list();
        if (!cancelled) setJobs(data);
      } catch (err) {
        if (!cancelled) setError(extractErrorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void loadJobs();
    const interval = setInterval(loadJobs, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  // Loaded once (not polled) - only needed for rerunning a job via
  // StartCollectionModal, same device/credential/type context the Devices
  // page uses to open it.
  useEffect(() => {
    let cancelled = false;
    async function loadDeviceContext() {
      try {
        const [devicesRes, typesRes, credsRes] = await Promise.all([
          devicesApi.list(),
          deviceTypesApi.list(),
          credentialsApi.list(),
        ]);
        if (cancelled) return;
        setDevices(devicesRes.data);
        setDeviceTypes(typesRes.data);
        setCredentials(credsRes.data);
      } catch (err) {
        if (!cancelled) setError(extractErrorMessage(err));
      }
    }
    void loadDeviceContext();
    return () => {
      cancelled = true;
    };
  }, []);

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    setSelected((prev) => (prev.size === jobs.length ? new Set() : new Set(jobs.map((j) => j.id))));
  }

  async function handleRerun(jobIds: string[]) {
    setError(null);
    setNotice(null);
    setRerunning(true);
    try {
      const details = await Promise.all(jobIds.map((id) => jobsApi.get(id)));
      const deviceIds = new Set<string>();
      for (const { data } of details) {
        for (const item of data.items) deviceIds.add(item.device_id);
      }

      const deviceMap = new Map(devices.map((d) => [d.id, d]));
      const targetDevices = Array.from(deviceIds)
        .map((id) => deviceMap.get(id))
        .filter((d): d is Device => !!d);

      if (targetDevices.length === 0) {
        setError("None of the devices from the selected job(s) still exist.");
        return;
      }
      const missing = deviceIds.size - targetDevices.length;
      if (missing > 0) {
        setNotice(
          `${missing} device(s) from the selected job(s) no longer exist and were skipped - rerunning the other ${targetDevices.length}.`
        );
      }
      setCollectionTarget(targetDevices);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setRerunning(false);
    }
  }

  return (
    <div className="page">
      <h1>Collection Jobs</h1>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="info-banner">{notice}</div>}

      {jobs.length > 0 && (
        <div className="page-header-row" style={{ marginBottom: 8 }}>
          <div className="selection-actions">
            <button
              disabled={selected.size === 0 || rerunning}
              onClick={() => handleRerun(Array.from(selected))}
            >
              {rerunning ? "Preparing…" : `Rerun selected (${selected.size})`}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <p>Loading…</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  checked={jobs.length > 0 && selected.size === jobs.length}
                  onChange={toggleSelectAll}
                />
              </th>
              <th>Started</th>
              <th>Status</th>
              <th>Devices</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.has(j.id)}
                    onChange={() => toggleSelected(j.id)}
                  />
                </td>
                <td>{new Date(j.created_at).toLocaleString()}</td>
                <td>
                  <StatusBadge status={j.status} />
                </td>
                <td>{j.item_count}</td>
                <td>
                  <Link to={`/jobs/${j.id}`}>View</Link>
                  <button
                    className="link-button"
                    style={{ marginLeft: 12 }}
                    disabled={rerunning}
                    onClick={() => handleRerun([j.id])}
                  >
                    Rerun
                  </button>
                </td>
              </tr>
            ))}
            {jobs.length === 0 && (
              <tr>
                <td colSpan={5} className="empty-state">
                  No collection jobs yet. Start one from the Devices page.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {collectionTarget && (
        <StartCollectionModal
          devices={collectionTarget}
          deviceTypes={deviceTypes}
          credentials={credentials}
          onClose={() => setCollectionTarget(null)}
          onStarted={(job) => {
            setCollectionTarget(null);
            setSelected(new Set());
            navigate(`/jobs/${job.id}`);
          }}
        />
      )}
    </div>
  );
}
