import { useEffect, useMemo, useState } from "react";

import { extractErrorMessage } from "../api/client";
import { saveBlobResponse } from "../api/download";
import { logHistoryApi } from "../api/logHistory";
import type { LogEntry, LogFile, LogHistory as History } from "../api/logHistory";
import { useAuth } from "../context/AuthContext";

const KIND_LABELS: Record<string, string> = { collection: "Collection", firmware: "File push", audit: "Wireless Audit", other: "Other" };

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Every collection, file push and Wireless Audit run, saved as a text file
 * on the server: find, read and download them; admins clear old ones. */
export function LogHistory() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [history, setHistory] = useState<History | null>(null);
  const [kind, setKind] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<LogFile | null>(null);
  const [openFind, setOpenFind] = useState("");
  const [purgeDays, setPurgeDays] = useState(30);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function load() {
    try {
      const { data } = await logHistoryApi.list({ kind: kind || undefined, q: query.trim() || undefined });
      setHistory(data);
      setSelected((prev) => new Set([...prev].filter((p) => data.files.some((f) => f.path === p))));
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useEffect(() => {
    const t = setTimeout(() => void load(), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, query]);

  const byDay = useMemo(() => {
    const groups = new Map<string, LogEntry[]>();
    for (const f of history?.files ?? []) groups.set(f.day, [...(groups.get(f.day) ?? []), f]);
    return [...groups.entries()];
  }, [history]);

  function toggle(paths: string[], on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const p of paths) {
        if (on) next.add(p);
        else next.delete(p);
      }
      return next;
    });
  }

  async function view(entry: LogEntry) {
    setError(null);
    try {
      const { data } = await logHistoryApi.read(entry.path);
      setOpen(data);
      setOpenFind("");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function act(work: () => Promise<string | null>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setNotice(await work());
      await load();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const chosen = [...selected];
  const openLines = useMemo(() => {
    if (!open) return "";
    const needle = openFind.trim().toLowerCase();
    if (!needle) return open.content;
    return open.content
      .split("\n")
      .filter((l) => l.toLowerCase().includes(needle))
      .join("\n");
  }, [open, openFind]);

  return (
    <div className="page page-wide">
      <div className="page-header-row">
        <div>
          <h1>Log history</h1>
          <p className="page-subtitle" style={{ marginTop: 4 }}>
            Every collection (per device: the session log and everything collected), file push and Wireless Audit run is saved as a
            text file on the server, in <span className="audit-mono">{history?.directory ?? "…"}</span>, one folder per day.
            {history
              ? history.retention_days > 0
                ? ` Files older than ${history.retention_days} days are removed automatically (LOG_ARCHIVE_RETENTION_DAYS).`
                : " Files are kept until cleared (LOG_ARCHIVE_RETENTION_DAYS=0)."
              : ""}
          </p>
        </div>
        <div className="page-actions">
          <button
            className="link-button"
            disabled={busy || chosen.length === 0}
            onClick={() =>
              void act(async () => {
                saveBlobResponse(await logHistoryApi.downloadZip(chosen), "packrat-logs.zip");
                return null;
              })
            }
          >
            Download selected ({chosen.length})
          </button>
          {isAdmin && (
            <button
              className="link-button"
              disabled={busy || chosen.length === 0}
              onClick={() => {
                if (!window.confirm(`Delete ${chosen.length} log file(s)? This cannot be undone.`)) return;
                void act(async () => {
                  const { data } = await logHistoryApi.remove(chosen);
                  if (open && chosen.includes(open.path)) setOpen(null);
                  return `Deleted ${data.deleted} file(s).`;
                });
              }}
            >
              Delete selected
            </button>
          )}
        </div>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="info-banner">{notice}</div>}

      <div className="page-header-row" style={{ marginTop: 6, alignItems: "center" }}>
        <div className="audit-filters">
          <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
            <option value="">Everything</option>
            <option value="collection">Collections</option>
            <option value="firmware">File pushes</option>
            <option value="audit">Wireless Audit runs</option>
          </select>
          <input type="search" placeholder="Device name or date (2026-10-16)" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        {history && (
          <span className="table-hint">
            {history.total_files} file(s), {size(history.total_bytes)} in all
          </span>
        )}
        {isAdmin && (
          <div className="audit-filters">
            <label className="inline-check" style={{ whiteSpace: "nowrap" }}>
              Clear older than
              <input type="number" min={0} max={3650} value={purgeDays} onChange={(e) => setPurgeDays(Math.max(0, Number(e.target.value) || 0))} style={{ width: 70 }} />
              days
            </label>
            <button
              disabled={busy}
              onClick={() => {
                const what = purgeDays === 0 ? "ALL saved log files" : `every log file older than ${purgeDays} days`;
                if (!window.confirm(`Delete ${what}? This cannot be undone.`)) return;
                void act(async () => {
                  const { data } = await logHistoryApi.purge(purgeDays);
                  setOpen(null);
                  return `Cleared ${data.deleted} file(s).`;
                });
              }}
            >
              Clear
            </button>
          </div>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: open ? "minmax(0, 2fr) minmax(0, 3fr)" : "1fr", gap: 16, marginTop: 10 }}>
        <div>
          {history && history.files.length === 0 && (
            <p className="table-hint">{history.total_files ? "No files match." : "No logs saved yet - they appear here as collections, pushes and audits finish."}</p>
          )}
          {byDay.map(([day, files]) => {
            const allOn = files.every((f) => selected.has(f.path));
            return (
              <table className="data-table" key={day} style={{ marginBottom: 14 }}>
                <thead>
                  <tr>
                    <th style={{ width: 28 }}>
                      <input type="checkbox" checked={allOn} onChange={(e) => toggle(files.map((f) => f.path), e.target.checked)} aria-label={`Select ${day}`} />
                    </th>
                    <th colSpan={4}>
                      {new Date(`${day}T00:00:00`).toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" })}
                      <span className="table-hint"> · {files.length} file(s)</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {files.map((f) => (
                    <tr key={f.path} style={open?.path === f.path ? { background: "var(--row-selected, rgba(80,140,255,0.12))" } : undefined}>
                      <td>
                        <input type="checkbox" checked={selected.has(f.path)} onChange={(e) => toggle([f.path], e.target.checked)} aria-label={`Select ${f.name}`} />
                      </td>
                      <td className="audit-mono">{new Date(f.modified_at).toLocaleTimeString()}</td>
                      <td>
                        <button className="link-button" style={{ padding: 0 }} onClick={() => void view(f)}>
                          {f.name}
                        </button>
                      </td>
                      <td>{KIND_LABELS[f.kind] ?? f.kind}</td>
                      <td className="table-hint">{size(f.size_bytes)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            );
          })}
        </div>
        {open && (
          <div>
            <div className="page-header-row" style={{ alignItems: "center" }}>
              <strong className="audit-mono">{open.path}</strong>
              <div className="audit-filters">
                <input type="search" placeholder="Show lines containing…" value={openFind} onChange={(e) => setOpenFind(e.target.value)} />
                <button
                  className="link-button"
                  onClick={() =>
                    void act(async () => {
                      saveBlobResponse(await logHistoryApi.download(open.path), open.path.split("/")[1]);
                      return null;
                    })
                  }
                >
                  Download
                </button>
                <button className="link-button" onClick={() => setOpen(null)}>
                  Close
                </button>
              </div>
            </div>
            {open.truncated && <p className="table-hint">Showing the first 2 MB of {size(open.size_bytes)} - download it for the rest.</p>}
            <pre className="audit-output" style={{ maxHeight: "70vh" }}>{openLines || "(no matching lines)"}</pre>
          </div>
        )}
      </div>
    </div>
  );
}
