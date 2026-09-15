import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { credentialsApi, devicesApi, deviceTypesApi, firmwareApi } from "../api/resources";
import type { Credential, Device, DeviceType, FirmwareImage, FirmwareJob } from "../api/types";
import { StartFirmwareUpgradeModal } from "../components/StartFirmwareUpgradeModal";
import { StatusBadge } from "../components/StatusBadge";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function Firmware() {
  const navigate = useNavigate();
  const [images, setImages] = useState<FirmwareImage[]>([]);
  const [jobs, setJobs] = useState<FirmwareJob[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceTypes, setDeviceTypes] = useState<DeviceType[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadLabel, setUploadLabel] = useState("");
  const [upgradeTarget, setUpgradeTarget] = useState<FirmwareImage | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function refresh() {
    setLoading(true);
    try {
      const [imagesRes, jobsRes, devicesRes, typesRes, credsRes] = await Promise.all([
        firmwareApi.listImages(),
        firmwareApi.listJobs(),
        devicesApi.list(),
        deviceTypesApi.list(),
        credentialsApi.list(),
      ]);
      setImages(imagesRes.data);
      setJobs(jobsRes.data);
      setDevices(devicesRes.data);
      setDeviceTypes(typesRes.data);
      setCredentials(credsRes.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function handleUpload(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    setUploading(true);
    try {
      await firmwareApi.uploadImage(file, uploadLabel.trim() || undefined);
      setUploadLabel("");
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  async function handleDeleteImage(id: string) {
    if (!confirm("Delete this firmware image? This can't be undone.")) return;
    setError(null);
    try {
      await firmwareApi.removeImage(id);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div className="page">
      <h1>Firmware</h1>
      {error && <div className="error-banner">{error}</div>}

      <div className="page-header-row">
        <h2 style={{ marginBottom: 0 }}>Images</h2>
        <div className="page-actions">
          <input
            value={uploadLabel}
            onChange={(e) => setUploadLabel(e.target.value)}
            placeholder="Label (optional, e.g. 17.9.5)"
            style={{ width: 220 }}
          />
          <label className="button-like">
            {uploading ? "Uploading…" : "Upload image"}
            <input ref={fileInputRef} type="file" onChange={handleUpload} disabled={uploading} hidden />
          </label>
        </div>
      </div>

      {loading ? (
        <p>Loading…</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>File</th>
              <th>Label</th>
              <th>Size</th>
              <th>Uploaded</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {images.map((img) => (
              <tr key={img.id}>
                <td>{img.original_filename}</td>
                <td>{img.label ?? "—"}</td>
                <td>{formatSize(img.size_bytes)}</td>
                <td>{new Date(img.created_at).toLocaleString()}</td>
                <td>
                  <button className="link-button" onClick={() => setUpgradeTarget(img)}>
                    Upgrade devices
                  </button>
                  <button
                    className="link-button danger"
                    style={{ marginLeft: 12 }}
                    onClick={() => handleDeleteImage(img.id)}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {images.length === 0 && (
              <tr>
                <td colSpan={5} className="empty-state">
                  No firmware images uploaded yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      <h2 style={{ marginTop: 32 }}>Upgrade jobs</h2>
      <table className="data-table">
        <thead>
          <tr>
            <th>Started</th>
            <th>Protocol</th>
            <th>Status</th>
            <th>Devices</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id}>
              <td>{new Date(j.created_at).toLocaleString()}</td>
              <td>{j.protocol.toUpperCase()}</td>
              <td>
                <StatusBadge status={j.status} />
              </td>
              <td>{j.item_count}</td>
              <td>
                <Link to={`/firmware/jobs/${j.id}`}>View</Link>
              </td>
            </tr>
          ))}
          {jobs.length === 0 && (
            <tr>
              <td colSpan={5} className="empty-state">
                No upgrade jobs yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {upgradeTarget && (
        <StartFirmwareUpgradeModal
          image={upgradeTarget}
          devices={devices}
          deviceTypes={deviceTypes}
          credentials={credentials}
          onClose={() => setUpgradeTarget(null)}
          onStarted={(job) => {
            setUpgradeTarget(null);
            navigate(`/firmware/jobs/${job.id}`);
          }}
        />
      )}
    </div>
  );
}
