import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { apiClient, extractErrorMessage } from "../api/client";
import { saveBlobResponse } from "../api/download";
import { credentialsApi, devicesApi, deviceTypesApi, jobsApi } from "../api/resources";
import type { Credential, Device, DeviceType, JobDetail as JobDetailType, NeighborGapCheck } from "../api/types";
import { DownloadOptions } from "../components/DownloadOptions";
import { JobStatusSummary } from "../components/JobStatusSummary";
import { LiveConsole } from "../components/LiveConsole";
import { RetryDeviceModal } from "../components/RetryDeviceModal";
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
  const [retryEditTarget, setRetryEditTarget] = useState<Device | null>(null);
  const [retryError, setRetryError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [neighborCheck, setNeighborCheck] = useState<NeighborGapCheck | null>(null);
  const [checkingNeighbors, setCheckingNeighbors] = useState(false);
  const [neighborError, setNeighborError] = useState<string | null>(null);

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
    setRetryEditTarget(device);
  }

  function handleRetryDeviceSaved(updated: Device) {
    setDevices((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
    setRetryEditTarget(null);
    setRetryTarget([updated]);
  }

  async function handleCancel() {
    if (!jobId) return;
    if (
      !confirm(
        "Cancel this job? Devices that haven't started yet will be skipped. A device still " +
          "connecting will finish that step first (it can't be interrupted mid-login); a device " +
          "already running commands stops after its current command instead of running the rest."
      )
    ) {
      return;
    }
    setCancelError(null);
    setCancelling(true);
    try {
      const { data } = await jobsApi.cancel(jobId);
      setJob(data);
    } catch (err) {
      setCancelError(extractErrorMessage(err));
    } finally {
      setCancelling(false);
    }
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
      // Backstop only - the server's Content-Disposition header (the
      // collection date, yyyymmdd) is what actually names the saved file.
      const today = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      saveBlobResponse(response, `${today}.zip`);
    } catch (err) {
      setDownloadError(extractErrorMessage(err));
    } finally {
      setDownloading(false);
    }
  }

  async function handleCheckNeighbors() {
    if (!jobId) return;
    setNeighborError(null);
    setCheckingNeighbors(true);
    try {
      const { data } = await jobsApi.checkNeighborGaps(jobId);
      setNeighborCheck(data);
    } catch (err) {
      setNeighborError(extractErrorMessage(err));
    } finally {
      setCheckingNeighbors(false);
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
  const wasActive = useRef(false);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | undefined;

    async function load() {
      try {
        const { data } = await jobsApi.get(jobId!);
        if (cancelled) return;
        setJob(data);
        const isActive = ACTIVE_STATUSES.has(data.status);
        if (!hasAutoExpanded.current && isActive) {
          hasAutoExpanded.current = true;
          expandAllConsoles(data.items);
        }
        // Auto-collapse the consoles once a job that was running finishes -
        // they were only opened to watch it live, and there's no need to
        // keep the output on screen once it's done.
        if (wasActive.current && !isActive) {
          setExpandedConsoles(new Set());
        }
        wasActive.current = isActive;
        if (!isActive && interval) {
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

  // Completed devices sink to the bottom so the ones still in progress (or
  // that need attention, like a failure) stay visible at the top without
  // scrolling past everything that's already done. Stable within each group.
  const sortedItems = useMemo(() => {
    if (!job) return [];
    const pending: typeof job.items = [];
    const done: typeof job.items = [];
    for (const item of job.items) {
      (item.status === "completed" ? done : pending).push(item);
    }
    return [...pending, ...done];
  }, [job]);

  if (error) return <div className="page error-banner">{error}</div>;
  if (!job) return <div className="page">Loading…</div>;

  return (
    <div className="page">
      <p>
        <Link to="/jobs">← Back to jobs</Link>
      </p>
      <div className="page-header-row sticky-header">
        <h1>Job {job.id.slice(0, 8)}</h1>
        <div className="page-actions">
          <StatusBadge status={job.status} />
          {ACTIVE_STATUSES.has(job.status) && (
            <button className="link-button danger" onClick={handleCancel} disabled={cancelling}>
              {cancelling ? "Cancelling…" : "Cancel job"}
            </button>
          )}
        </div>
      </div>
      <p className="page-subtitle">
        Started {new Date(job.created_at).toLocaleString()}
        {job.finished_at && ` · Finished ${new Date(job.finished_at).toLocaleString()}`}
      </p>

      {downloadError && <div className="error-banner">{downloadError}</div>}
      {retryError && <div className="error-banner">{retryError}</div>}
      {cancelError && <div className="error-banner">{cancelError}</div>}

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

      {job.items.some((i) => i.snapshot_id) && (
        <div className="page-header-row" style={{ marginBottom: 16 }}>
          <p className="page-subtitle" style={{ margin: 0 }}>
            Cross-check CDP/LLDP neighbors these devices reported against your device inventory.
          </p>
          <button onClick={handleCheckNeighbors} disabled={checkingNeighbors}>
            {checkingNeighbors ? "Checking…" : "Check for untracked neighbors"}
          </button>
        </div>
      )}

      {neighborError && <div className="error-banner">{neighborError}</div>}

      {neighborCheck && (
        <div className="card-form" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>Untracked neighbors</h3>
          {neighborCheck.checked_item_count === 0 ? (
            <p className="page-subtitle" style={{ marginTop: 0 }}>
              None of this job's devices ran a CDP/LLDP "detail" command, so there's nothing to check.
            </p>
          ) : neighborCheck.missing.length === 0 ? (
            <p className="page-subtitle" style={{ marginTop: 0 }}>
              No untracked neighbors found across {neighborCheck.checked_item_count} device
              {neighborCheck.checked_item_count === 1 ? "" : "s"} checked.
            </p>
          ) : (
            <>
              <p className="field-hint" style={{ marginTop: 0 }}>
                Seen on the wire via CDP/LLDP but not in your device inventory - names are best-effort
                (an abbreviated or differently-formatted name can look "missing" when it isn't).
              </p>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>IP</th>
                    <th>Protocol</th>
                    <th>Seen from</th>
                  </tr>
                </thead>
                <tbody>
                  {neighborCheck.missing.map((n) => (
                    <tr key={n.name}>
                      <td>{n.name}</td>
                      <td>{n.ip ?? "—"}</td>
                      <td>{n.protocols.join(", ")}</td>
                      <td>{n.seen_from.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
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
          {sortedItems.map((item) => (
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
                  {(item.status === "failed" || item.status === "cancelled") && item.device_id && (
                    <button
                      className="link-button"
                      style={{ marginLeft: 12 }}
                      onClick={() => handleRetry(item.device_id!)}
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

      {retryEditTarget && (
        <RetryDeviceModal
          device={retryEditTarget}
          credentials={credentials}
          onClose={() => setRetryEditTarget(null)}
          onSaved={handleRetryDeviceSaved}
        />
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
