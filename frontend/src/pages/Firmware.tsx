import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { credentialsApi, devicesApi, firmwareApi } from "../api/resources";
import type { Credential, Device, FirmwareImage } from "../api/types";
import { PushFirmwareModal } from "../components/PushFirmwareModal";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

export function Firmware() {
  const navigate = useNavigate();
  const [images, setImages] = useState<FirmwareImage[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [description, setDescription] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);

  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [pushTarget, setPushTarget] = useState<FirmwareImage | null>(null);

  async function loadAll() {
    try {
      const [imagesRes, devicesRes, credsRes] = await Promise.all([
        firmwareApi.list(),
        devicesApi.list(),
        credentialsApi.list(),
      ]);
      setImages(imagesRes.data);
      setDevices(devicesRes.data);
      setCredentials(credsRes.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAll();
  }, []);

  async function handleUpload() {
    const file = fileInputRef.current?.files?.[0];
    if (!file) {
      setError("Choose a firmware file to upload first.");
      return;
    }
    setError(null);
    setUploading(true);
    setUploadProgress(0);
    try {
      const { data } = await firmwareApi.upload(file, description, setUploadProgress);
      setImages((prev) => [data, ...prev]);
      setDescription("");
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this firmware image? This removes the uploaded file - it can't be undone.")) return;
    setError(null);
    setDeletingId(id);
    try {
      await firmwareApi.remove(id);
      setImages((prev) => prev.filter((i) => i.id !== id));
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="page">
      <h1>Firmware</h1>
      <p className="page-subtitle">
        Upload Cisco firmware images here, then push them to devices over SCP - see the "Push" action
        on each image below. Pushes are driven the same way collection jobs are: this app connects to
        each device over SSH and runs the copy itself.
      </p>
      {error && <div className="error-banner">{error}</div>}

      <div className="card-form" style={{ marginBottom: 24 }}>
        <h3 style={{ marginTop: 0 }}>Upload firmware image</h3>
        <label style={{ marginBottom: 14 }}>
          File
          <input ref={fileInputRef} type="file" disabled={uploading} />
        </label>
        <label style={{ marginBottom: 14 }}>
          Description (optional)
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="e.g. IOS-XE 17.9.5 for the 9300 stack"
            disabled={uploading}
          />
        </label>
        <button onClick={handleUpload} disabled={uploading}>
          {uploading ? `Uploading… ${uploadProgress}%` : "Upload"}
        </button>
      </div>

      {loading ? (
        <p>Loading…</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Filename</th>
              <th>Description</th>
              <th>Size</th>
              <th>MD5</th>
              <th>Uploaded</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {images.map((image) => (
              <tr key={image.id}>
                <td>{image.filename}</td>
                <td>{image.description ?? "—"}</td>
                <td>{formatBytes(image.size_bytes)}</td>
                <td>
                  <code style={{ fontSize: 12 }}>{image.md5}</code>
                </td>
                <td>{new Date(image.created_at).toLocaleString()}</td>
                <td>
                  <button className="link-button" onClick={() => setPushTarget(image)}>
                    Push
                  </button>
                  <button
                    className="link-button danger"
                    style={{ marginLeft: 12 }}
                    disabled={deletingId === image.id}
                    onClick={() => handleDelete(image.id)}
                  >
                    {deletingId === image.id ? "Deleting…" : "Delete"}
                  </button>
                </td>
              </tr>
            ))}
            {images.length === 0 && (
              <tr>
                <td colSpan={6} className="empty-state">
                  No firmware images uploaded yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {pushTarget && (
        <PushFirmwareModal
          image={pushTarget}
          devices={devices}
          credentials={credentials}
          onClose={() => setPushTarget(null)}
          onStarted={(job) => {
            setPushTarget(null);
            navigate(`/firmware-jobs/${job.id}`);
          }}
        />
      )}
    </div>
  );
}
