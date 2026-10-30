import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { dnsCheckApi } from "../api/resources";
import type { DnsCheckJobDetail as DnsCheckJobDetailType, DnsCheckJobItem } from "../api/types";
import { JobStatusSummary } from "../components/JobStatusSummary";
import { StatusBadge } from "../components/StatusBadge";

const ACTIVE_STATUSES = new Set(["pending", "running"]);
const MONO = { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" };

function Pill({ ok, okLabel, failLabel }: { ok: boolean | null; okLabel: string; failLabel: string }) {
  if (ok === null) return <span className="field-hint">—</span>;
  return <span className={`status-badge status-${ok ? "completed" : "failed"}`}>{ok ? okLabel : failLabel}</span>;
}

function looksLikeIpAddress(s: string): boolean {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(s) || s.includes(":");
}

function Tile({ label, value, tone, hint }: { label: string; value: number; tone?: "up" | "down" | "unknown" | "neutral"; hint: string }) {
  return (
    <div className={`monitor-tile monitor-tile-${tone ?? "neutral"}`} title={hint}>
      <span className="monitor-tile-label">{label}</span>
      <span className="monitor-tile-value">{value.toLocaleString()}</span>
    </div>
  );
}

const done = (i: DnsCheckJobItem) => i.status === "completed";
const inUse = (i: DnsCheckJobItem) => i.ping_ok === true || i.reverse_ok === true;
const free = (i: DnsCheckJobItem) => done(i) && !inUse(i);
const liveWithoutPtr = (i: DnsCheckJobItem) => i.ping_ok === true && i.reverse_ok === false;
const ptrMismatch = (i: DnsCheckJobItem) => i.dns_matches === false;
const ptrNoReply = (i: DnsCheckJobItem) => done(i) && i.ping_ok === false && i.reverse_ok === true;

type Filter = "all" | "in_use" | "free" | "no_ptr" | "mismatch" | "no_reply";
const FILTERS: { key: Filter; label: string; test: (i: DnsCheckJobItem) => boolean }[] = [
  { key: "all", label: "Every address", test: () => true },
  { key: "in_use", label: "In use", test: inUse },
  { key: "free", label: "Free", test: free },
  { key: "no_ptr", label: "Answering, no PTR record", test: liveWithoutPtr },
  { key: "mismatch", label: "PTR name does not point back", test: ptrMismatch },
  { key: "no_reply", label: "In DNS, no ping reply", test: ptrNoReply },
];

/** Whether the PTR name of an IP target resolves back to it. */
function ForwardCheck({ item }: { item: DnsCheckJobItem }) {
  if (item.dns_matches === true) {
    return <span className="status-badge status-completed" title={`${item.reverse_hostname} resolves to ${item.forward_ips.join(", ")}`}>Points back</span>;
  }
  if (item.dns_matches === false) {
    return item.forward_ok ? (
      <span title="The PTR name resolves to a different address: one of the two DNS records is probably out of date">
        <span className="status-badge status-failed">Mismatch</span> <span className="table-hint">name points to {item.forward_ips.join(", ")}</span>
      </span>
    ) : (
      <span title="The PTR record names a host that has no A/AAAA record">
        <span className="status-badge status-failed">Mismatch</span> <span className="table-hint">name does not resolve</span>
      </span>
    );
  }
  return <span className="field-hint">{done(item) ? "no PTR to check" : "—"}</span>;
}

function csvCell(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

function downloadCsv(job: DnsCheckJobDetailType) {
  const yesNo = (v: boolean | null) => (v === null ? "" : v ? "yes" : "no");
  const header = job.subnet
    ? ["Address", "In use", "Ping reply", "PTR record", "PTR name resolves to", "PTR points back", "Status"]
    : ["Target", "Ping reply", "Forward DNS", "PTR record", "PTR points back", "Status"];
  const rows = job.items.map((i) =>
    job.subnet
      ? [i.target, done(i) ? yesNo(inUse(i)) : "", yesNo(i.ping_ok), i.reverse_hostname ?? "", i.forward_ips.join(" "), yesNo(i.dns_matches), i.status]
      : [i.target, yesNo(i.ping_ok), i.forward_ips.join(" "), i.reverse_hostname ?? "", yesNo(i.dns_matches), i.status],
  );
  const text = [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n";
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  const stamp = new Date(job.created_at).toISOString().slice(0, 16).replace(/[-:T]/g, "");
  a.download = job.subnet ? `dns-sweep-${job.subnet.replace(/[/:]/g, "_")}-${stamp}.csv` : `dns-check-${job.id.slice(0, 8)}-${stamp}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function DnsCheckJobDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<DnsCheckJobDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  // null: "In use" once a sweep has finished, every address while it runs.
  const [filter, setFilter] = useState<Filter | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | undefined;

    async function load() {
      try {
        const { data } = await dnsCheckApi.getJob(jobId!);
        if (cancelled) return;
        setJob(data);
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

  const counts = useMemo(() => {
    const items = job?.items ?? [];
    return Object.fromEntries(FILTERS.map((f) => [f.key, items.filter(f.test).length])) as Record<Filter, number>;
  }, [job]);

  async function handleCancel() {
    if (!jobId) return;
    if (!confirm("Cancel this DNS check? Targets not yet checked are skipped; one already in flight finishes normally.")) {
      return;
    }
    setCancelError(null);
    setCancelling(true);
    try {
      const { data } = await dnsCheckApi.cancelJob(jobId);
      setJob(data);
    } catch (err) {
      setCancelError(extractErrorMessage(err));
    } finally {
      setCancelling(false);
    }
  }

  if (error) return <div className="page error-banner">{error}</div>;
  if (!job) return <div className="page">Loading…</div>;

  const active = ACTIVE_STATUSES.has(job.status);
  const sweep = job.subnet !== null;
  const effectiveFilter: Filter = sweep ? (filter ?? (active ? "all" : "in_use")) : "all";
  const test = FILTERS.find((f) => f.key === effectiveFilter)!.test;
  const rows = job.items.filter(test);

  return (
    <div className="page">
      <p>
        <Link to="/dns-check">← Back to DNS Check</Link>
      </p>
      <div className="page-header-row sticky-header">
        <h1>
          {sweep ? (
            <>
              Sweep of <span style={MONO}>{job.subnet}</span>
            </>
          ) : (
            `DNS check ${job.id.slice(0, 8)}`
          )}
        </h1>
        <div className="page-actions">
          <StatusBadge status={job.status} />
          {active && (
            <button className="link-button danger" onClick={handleCancel} disabled={cancelling}>
              {cancelling ? "Cancelling…" : "Cancel"}
            </button>
          )}
          <button className="link-button" onClick={() => downloadCsv(job)} disabled={job.items.length === 0}>
            Download CSV
          </button>
        </div>
      </div>
      <p className="page-subtitle">
        Started {new Date(job.created_at).toLocaleString()}
        {job.finished_at && ` · Finished ${new Date(job.finished_at).toLocaleString()}`}
        {sweep && ` · ${job.item_count.toLocaleString()} usable addresses`}
      </p>

      {cancelError && <div className="error-banner">{cancelError}</div>}

      <JobStatusSummary items={job.items} />

      {sweep && (
        <>
          <div className="monitor-tiles" role="group" aria-label="Sweep summary">
            <Tile label="In use" value={counts.in_use} tone="up" hint="Answered a ping, has a PTR record, or both" />
            <Tile label="Free" value={counts.free} hint="No ping reply and no PTR record: probably unused, but a host that blocks ping and has no DNS record looks the same" />
            <Tile label="Answering, no PTR" value={counts.no_ptr} tone={counts.no_ptr ? "unknown" : "neutral"} hint="Live hosts missing from reverse DNS" />
            <Tile label="PTR mismatch" value={counts.mismatch} tone={counts.mismatch ? "down" : "neutral"} hint="The PTR name resolves to a different address, or not at all: a stale record" />
            <Tile label="In DNS, no reply" value={counts.no_reply} tone={counts.no_reply ? "unknown" : "neutral"} hint="Has a PTR record but did not answer: switched off, blocking ping, or a record left behind" />
          </div>
          <div className="wireless-toolbar">
            <label>
              Show{" "}
              <select id="sweep-filter" value={effectiveFilter} onChange={(e) => setFilter(e.target.value as Filter)}>
                {FILTERS.map((f) => (
                  <option key={f.key} value={f.key}>
                    {f.label} ({counts[f.key].toLocaleString()})
                  </option>
                ))}
              </select>
            </label>
            <span className="wireless-toolbar-summary">
              {rows.length.toLocaleString()} of {job.items.length.toLocaleString()} addresses
            </span>
          </div>
        </>
      )}

      <table className="data-table" style={{ marginTop: sweep ? 8 : undefined }}>
        <thead>
          <tr>
            <th>{sweep ? "Address" : "Target"}</th>
            <th>Status</th>
            <th>Ping</th>
            {!sweep && <th>Forward DNS</th>}
            <th>Reverse DNS (PTR)</th>
            <th title="Does the PTR name resolve back to this address?">PTR check</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((item) => {
            const isIp = looksLikeIpAddress(item.target);
            return (
              <tr key={item.id}>
                <td style={sweep ? MONO : undefined}>{item.target}</td>
                <td>
                  <StatusBadge status={item.status} />
                </td>
                <td>
                  <Pill ok={item.ping_ok} okLabel="Reachable" failLabel="No reply" />
                </td>
                {!sweep && (
                  <td>
                    {isIp ? (
                      <span className="field-hint">{done(item) ? "N/A (already an IP)" : "—"}</span>
                    ) : item.forward_ok ? (
                      item.forward_ips.join(", ")
                    ) : done(item) ? (
                      <span className="field-hint">no DNS record found</span>
                    ) : (
                      <span className="field-hint">—</span>
                    )}
                  </td>
                )}
                <td>
                  {item.reverse_ok ? (
                    item.reverse_hostname
                  ) : done(item) ? (
                    <span className="field-hint">no PTR record</span>
                  ) : (
                    <span className="field-hint">—</span>
                  )}
                </td>
                <td>{isIp ? <ForwardCheck item={item} /> : <span className="field-hint">—</span>}</td>
              </tr>
            );
          })}
          {rows.length === 0 && (
            <tr>
              <td colSpan={sweep ? 5 : 6} className="empty-state">
                {job.items.length === 0
                  ? "No targets in this job."
                  : active
                    ? "Nothing matches yet; the sweep is still running."
                    : "No addresses match this filter."}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
