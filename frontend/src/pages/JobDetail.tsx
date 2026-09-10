import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { apiClient, extractErrorMessage } from "../api/client";
import { saveBlobResponse } from "../api/download";
import { credentialsApi, devicesApi, deviceTypesApi, jobsApi } from "../api/resources";
import type { Credential, Device, DeviceType, JobDetail as JobDetailType } from "../api/types";
import { DownloadOptions } from "../components/DownloadOptions";
import { JobStatusSummary } from "../components/JobStatusSummary";
import { LiveConsole } from "../components/LiveConsole";
import { SnapshotModal } from "../components/SnapshotModal";
import { StartCollectionModal } from "../components/StartCollectionModal";
import { StatusBadge } from "../components/StatusBadge";
import { useDownloadPrefs } from "../hooks/useDownloadPrefs";

const ACTIVE_STATUSES = new Set(["pending", "running"]);

export function JobDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const [job, setJob] = useState<JobDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openSnapshotId, setOpenSnapshotId] = useState<string | null>(null);
  const [expandedConsoles, setExpandedConsoles] = useState<Set<string>>(new Set());
  const { prefs, updatePrefs } = useDownloadPrefs();
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  // Loaded once (not polled) - only needed so a failed item's "Retry" can
  // open the same Start collection dialog used elsewhere, for just that
  // one device.
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceTypes, setDeviceTypes] = useState<DeviceType[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [retryTarget, setRetryTarget] = useState<Device[] | null>(null);
  const [retryError, setRetryError] = useState<string | null>(null);

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
      } catch {
        // Retry is a bonus action - a failure here shouldn't block viewing
        // the job itself.
      }
    }
    void loadDeviceContext();
    return () => {
      cancelled = true;
    };
  }, []);

  function handleRetry(deviceId: string) {
    setRetryError(null);
    const device = devices.find((d) => d.id === deviceId);
    if (!device) {
      setRetryError("That device no longer exists, so it can't be retried.");
      return;
    }
    setRetryTarget([device]);
  }

  async function handleDownloadAll() {
    if (!jobId) return;
    setDownloadError(null);
    setDownloading(true);
    try {
      const response = await apiClient.get(`/api/jobs/${jobId}/download`, {
        responseType: "blob",
        params: { ext: prefs.extension, include_timestamp: prefs.includeTimestamp },
      });
      saveBlobResponse(response, `job-${jobId.slice(0, 8)}-configs.zip`);
    } catch (err) {
      setDownloadError(extractErrorMessage(err));
    } finally {
      setDownloading(false);
    }
  }

  function toggleConsole(itemId: string) {
    setExpandedConsoles((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  }

  function expandAllConsoles(items: JobDetailType["items"]) {
    setExpandedConsoles(new Set(items.map((i) => i.id)));
  }

  // The first time a still-running job loads, show every device's console
  // right away instead of making the viewer click each one - the point is
  // to watch all the switches being collected from at once, live. Only
  // fires once, so a console the viewer collapses afterward stays
  // collapsed on the next poll instead of snapping back open.
  const hasAutoExpanded = useRef(false);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | undefined;

    async function load() {
      try {
        const { data } = await jobsApi.get(jobId!);
        if (cancelled) return;
        setJob(data);
        if (!hasAutoExpanded.current && ACTIVE_STATUSES.has(data.status)) {
          hasAutoExpanded.current = true;
          expandAllConsoles(data.items);
        }
        if (!ACTIVE_STATUSES.has(data.status) && interval) {
          clearInterval(interval);
        }
      } catch (err) {
        if (!cancelled) setError(extractErrorMessage(err));
      }
    }

    void load();
    interval = setInterval(load, 3000);
    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
    };
  }, [jobId]);

  if (error) return <div className="page error-banner">{error}</div>;
  if (!job) return <div className="page">Loading…</div>;

  return (
    <div className="page">
      <p>
        <Link to="/jobs">← Back to jobs</Link>
      </p>
      <div className="page-header-row">
        <h1>Job {job.id.slice(0, 8)}</h1>
        <StatusBadge status={job.status} />
      </div>
      <p className="page-subtitle">
        Started {new Date(job.created_at).toLocaleString()}
        {job.finished_at && ` · Finished ${new Date(job.finished_at).toLocaleString()}`}
      </p>

      {downloadError && <div className="error-banner">{downloadError}</div>}
      {retryError && <div className="error-banner">{retryError}</div>}

      <JobStatusSummary items={job.items} />

      {job.items.length > 1 && (
        <p style={{ marginTop: -8 }}>
          <button
            className="link-button"
            onClick={() =>
              expandedConsoles.size === job.items.length
                ? setExpandedConsoles(new Set())
                : expandAllConsoles(job.items)
            }
          >
            {expandedConsoles.size === job.items.length ? "Collapse all consoles" : "Expand all consoles"}
          </button>
        </p>
      )}

      {job.items.some((i) => i.snapshot_id) && (
        <div className="page-header-row download-footer" style={{ marginBottom: 16 }}>
          <DownloadOptions prefs={prefs} onChange={updatePrefs} />
          <button onClick={handleDownloadAll} disabled={downloading}>
            {downloading ? "Preparing…" : "Download all"}
          </button>
        </div>
      )}

      {job.items.some((i) => i.status === "authenticating") && (
        <div className="info-banner">
          One or more devices are logging in - if their credential uses push-based MFA, approve the
          request on your phone now. This can take up to a minute; no commands run until it succeeds.
        </div>
      )}

      <table className="data-table">
        <thead>
          <tr>
            <th>Device</th>
            <th>Status</th>
            <th>Error</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {job.items.map((item) => (
            <Fragment key={item.id}>
              <tr>
                <td>{item.device_name}</td>
                <td>
                  <StatusBadge status={item.status} />
                  {item.used_fallback_credential && (
                    <span className="status-badge status-fallback" title="Primary credential failed; the fallback credential was used instead">
                      fallback used
                    </span>
                  )}
                </td>
                <td>{item.error_message ?? "—"}</td>
                <td>
                  <button className="link-button" onClick={() => toggleConsole(item.id)}>
                    {expandedConsoles.has(item.id) ? "Hide console" : "Console"}
                  </button>
                  {item.snapshot_id && (
                    <button
                      className="link-button"
                      style={{ marginLeft: 12 }}
                      onClick={() => setOpenSnapshotId(item.snapshot_id)}
                    >
                      View config
                    </button>
                  )}
                  {item.status === "failed" && (
                    <button
                      className="link-button"
                      style={{ marginLeft: 12 }}
                      onClick={() => handleRetry(item.device_id)}
                    >
                      Retry
                    </button>
                  )}
                </td>
              </tr>
              {expandedConsoles.has(item.id) && (
                <tr className="console-row">
                  <td colSpan={4}>
                    <LiveConsole output={item.live_output} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>

      {openSnapshotId && (
        <SnapshotModal snapshotId={openSnapshotId} onClose={() => setOpenSnapshotId(null)} />
      )}

      {retryTarget && (
        <StartCollectionModal
          devices={retryTarget}
          deviceTypes={deviceTypes}
          credentials={credentials}
          onClose={() => setRetryTarget(null)}
          onStarted={(newJob) => {
            setRetryTarget(null);
            navigate(`/jobs/${newJob.id}`);
          }}
        />
      )}
    </div>
  );
}
