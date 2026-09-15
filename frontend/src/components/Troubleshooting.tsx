import { useCallback, useEffect, useRef, useState } from "react";

import { extractErrorMessage } from "../api/client";
import { saveBlobResponse } from "../api/download";
import { logsApi } from "../api/resources";
import type { LogOverview } from "../api/types";
import { useAuth } from "../context/AuthContext";

const LINE_CHOICES = [100, 200, 500, 1000, 2000];

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function describeFile(name: string): string {
  const base = name.replace(/\.log(\.\d+)?$/, "");
  const rotated = /\.log\.\d+$/.test(name) ? " (older, rotated)" : "";
  if (base.endsWith("-api")) return `Web/API server${rotated}`;
  if (base.endsWith("-worker")) return `Worker: collections, pushes, polls${rotated}`;
  if (base.endsWith("-beat")) return `Scheduler (beat)${rotated}`;
  return rotated.trim();
}

/** Settings > Troubleshooting: the application's log files - what's on
 * disk, a live tail of any of them, and a one-click zip of everything to
 * send along with a problem report. Admins only. */
export function Troubleshooting() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [overview, setOverview] = useState<LogOverview | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [lines, setLines] = useState(200);
  const [content, setContent] = useState("");
  const [follow, setFollow] = useState(false);
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const consoleRef = useRef<HTMLPreElement>(null);

  const loadOverview = useCallback(async () => {
    try {
      const { data } = await logsApi.overview();
      setOverview(data);
      if (data.files.length && !data.files.some((f) => f.name === selected)) {
        setSelected(data.files.find((f) => f.name === "packrat-api.log")?.name ?? data.files[0].name);
      }
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }, [selected]);

  const loadTail = useCallback(async () => {
    if (!selected) return;
    setLoading(true);
    try {
      const { data } = await logsApi.tail(selected, lines);
      setContent(data.content);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [selected, lines]);

  useEffect(() => {
    if (isAdmin) void loadOverview();
  }, [isAdmin, loadOverview]);

  useEffect(() => {
    if (isAdmin && selected) void loadTail();
  }, [isAdmin, selected, lines, loadTail]);

  useEffect(() => {
    if (!follow || !isAdmin) return;
    const timer = window.setInterval(() => {
      void loadTail();
      void loadOverview();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [follow, isAdmin, loadTail, loadOverview]);

  useEffect(() => {
    if (follow && consoleRef.current) consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
  }, [content, follow]);

  async function handleDownload() {
    setDownloading(true);
    try {
      const response = await logsApi.download();
      saveBlobResponse(response, "packrat-logs.zip");
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setDownloading(false);
    }
  }

  if (!isAdmin) return null;

  const shown = errorsOnly
    ? content
        .split("\n")
        .filter((line) => / (WARNING|ERROR|CRITICAL) /.test(line) || /^(Traceback|  File |    |[A-Za-z]+Error: )/.test(line))
        .join("\n")
    : content;

  return (
    <div className="card-form" style={{ marginTop: 24 }} id="troubleshooting">
      <h3 style={{ marginTop: 0 }}>Troubleshooting</h3>
      <p className="page-subtitle" style={{ marginTop: 0 }}>
        Packrat writes everything it does - every request, every device it talks to, every error with its full
        traceback - to log files on the server. When something goes wrong, download the bundle and send it along
        with a description of what you were doing; the reference code shown in a red error message can be found
        in the log next to the cause.
      </p>

      {error && <div className="error-banner">{error}</div>}

      {overview && !overview.enabled && (
        <p className="page-subtitle">
          Viewing logs from the app is turned off on this server (<code>LOG_DOWNLOAD_ENABLED=false</code>). The files
          are in <code>{overview.directory}</code> on the server.
        </p>
      )}

      {overview && overview.enabled && (
        <>
          <div className="page-header-row" style={{ alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <span className="page-subtitle" style={{ margin: 0 }}>
              Log folder <code>{overview.directory}</code> · level {overview.level}
            </span>
            <button type="button" onClick={handleDownload} disabled={downloading} style={{ marginLeft: "auto" }}>
              {downloading ? "Preparing…" : "Download log bundle (.zip)"}
            </button>
          </div>

          <table className="data-table" style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>File</th>
                <th>What it holds</th>
                <th>Size</th>
                <th>Last written</th>
              </tr>
            </thead>
            <tbody>
              {overview.files.length === 0 ? (
                <tr>
                  <td className="empty-state" colSpan={4}>
                    No log files yet.
                  </td>
                </tr>
              ) : (
                overview.files.map((file) => (
                  <tr
                    key={file.name}
                    onClick={() => setSelected(file.name)}
                    style={{ cursor: "pointer", fontWeight: file.name === selected ? 600 : undefined }}
                  >
                    <td>
                      <code>{file.name}</code>
                    </td>
                    <td>{describeFile(file.name)}</td>
                    <td>{formatSize(file.size_bytes)}</td>
                    <td>{new Date(file.modified_at).toLocaleString()}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>

          {selected && (
            <>
              <div className="page-header-row" style={{ alignItems: "center", gap: 12, flexWrap: "wrap", marginTop: 16 }}>
                <label className="inline-label" style={{ margin: 0 }}>
                  Showing last
                  <select value={lines} onChange={(e) => setLines(Number(e.target.value))}>
                    {LINE_CHOICES.map((n) => (
                      <option key={n} value={n}>
                        {n} lines
                      </option>
                    ))}
                  </select>
                  of <code>{selected}</code>
                </label>
                <label className="inline-label" style={{ margin: 0 }}>
                  <input type="checkbox" checked={errorsOnly} onChange={(e) => setErrorsOnly(e.target.checked)} />
                  Warnings and errors only
                </label>
                <label className="inline-label" style={{ margin: 0 }}>
                  <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
                  Follow (refresh every 5 s)
                </label>
                <button type="button" className="link-button" onClick={() => void loadTail()} disabled={loading} style={{ marginLeft: "auto" }}>
                  {loading ? "Refreshing…" : "Refresh"}
                </button>
              </div>
              <pre className="config-view" ref={consoleRef} style={{ marginTop: 8, maxHeight: 420, borderRadius: 8 }}>
                {shown || (loading ? "Loading…" : errorsOnly ? "No warnings or errors in the lines shown." : "The file is empty.")}
              </pre>
            </>
          )}
        </>
      )}
    </div>
  );
}
