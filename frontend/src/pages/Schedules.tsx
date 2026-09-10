import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { devicesApi, schedulesApi } from "../api/resources";
import type { Device, Schedule, ScheduleFrequency } from "../api/types";

function describeFrequency(s: Schedule): string {
  if (s.frequency === "every_n_hours") return `Every ${s.interval_hours} hour${s.interval_hours === 1 ? "" : "s"}`;
  const hh = String(s.run_at_hour).padStart(2, "0");
  const mm = String(s.run_at_minute).padStart(2, "0");
  return `Daily at ${hh}:${mm} UTC`;
}

function describeDevices(s: Schedule, deviceCount: number, deviceNameById: Map<string, string>): string {
  if (s.device_ids === null) return `All devices (${deviceCount})`;
  const names = s.device_ids.map((id) => deviceNameById.get(id) ?? "deleted device");
  return names.join(", ") || "0 devices";
}

export function Schedules() {
  const navigate = useNavigate();
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [frequency, setFrequency] = useState<ScheduleFrequency>("daily");
  const [intervalHours, setIntervalHours] = useState("6");
  const [runAtHour, setRunAtHour] = useState("2");
  const [runAtMinute, setRunAtMinute] = useState("0");
  const [scope, setScope] = useState<"all" | "specific">("all");
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);

  const deviceCount = devices.length;
  const deviceNameById = useMemo(() => new Map(devices.map((d) => [d.id, d.name])), [devices]);

  async function refresh() {
    setLoading(true);
    try {
      const [schedulesRes, devicesRes] = await Promise.all([schedulesApi.list(), devicesApi.list()]);
      setSchedules(schedulesRes.data);
      setDevices(devicesRes.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  function resetForm() {
    setEditingId(null);
    setName("");
    setFrequency("daily");
    setIntervalHours("6");
    setRunAtHour("2");
    setRunAtMinute("0");
    setScope("all");
    setSelectedDeviceIds(new Set());
  }

  function toggleForm() {
    if (showForm) resetForm();
    setShowForm((v) => !v);
  }

  function handleEdit(s: Schedule) {
    setEditingId(s.id);
    setName(s.name);
    setFrequency(s.frequency);
    setIntervalHours(String(s.interval_hours ?? "6"));
    setRunAtHour(String(s.run_at_hour ?? "2"));
    setRunAtMinute(String(s.run_at_minute ?? "0"));
    if (s.device_ids === null) {
      setScope("all");
      setSelectedDeviceIds(new Set());
    } else {
      setScope("specific");
      setSelectedDeviceIds(new Set(s.device_ids));
    }
    setShowForm(true);
  }

  function toggleDevice(id: string) {
    setSelectedDeviceIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const base = {
        name,
        frequency,
        interval_hours: frequency === "every_n_hours" ? Number(intervalHours) || 1 : undefined,
        run_at_hour: frequency === "daily" ? Number(runAtHour) : undefined,
        run_at_minute: frequency === "daily" ? Number(runAtMinute) : undefined,
      };
      const deviceIds = scope === "specific" ? Array.from(selectedDeviceIds) : undefined;
      if (editingId) {
        await schedulesApi.update(editingId, {
          ...base,
          device_ids: deviceIds,
          clear_device_ids: scope === "all",
        });
      } else {
        await schedulesApi.create({ ...base, device_ids: deviceIds });
      }
      resetForm();
      setShowForm(false);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleToggleEnabled(s: Schedule) {
    setError(null);
    try {
      await schedulesApi.update(s.id, { enabled: !s.enabled });
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this schedule? It won't run again.")) return;
    setError(null);
    try {
      await schedulesApi.remove(id);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleRunNow(id: string) {
    setError(null);
    try {
      const { data } = await schedulesApi.runNow(id);
      navigate(`/jobs/${data.id}`);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div className="page">
      <div className="page-header-row">
        <h1>Schedules</h1>
        <div className="page-actions">
          <button onClick={toggleForm}>{showForm ? "Cancel" : "Add schedule"}</button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}
      <p className="page-subtitle" style={{ marginTop: 0 }}>
        Recurring collection runs - each one creates an ordinary job on the Jobs page. Unattended, so a
        device whose credential needs a one-time passcode will fail on scheduled runs (use "Run now" or a
        manual collection for those instead).
      </p>

      {showForm && (
        <form className="card-form" onSubmit={handleSubmit}>
          {editingId && <p className="field-hint" style={{ marginTop: 0 }}>Editing {name || "schedule"}</p>}
          <div className="form-grid">
            <label>
              Name
              <input value={name} onChange={(e) => setName(e.target.value)} required />
            </label>
            <label>
              Frequency
              <select value={frequency} onChange={(e) => setFrequency(e.target.value as ScheduleFrequency)}>
                <option value="daily">Daily</option>
                <option value="every_n_hours">Every N hours</option>
              </select>
            </label>
            {frequency === "every_n_hours" ? (
              <label>
                Every how many hours
                <input
                  type="number"
                  min={1}
                  value={intervalHours}
                  onChange={(e) => setIntervalHours(e.target.value)}
                />
              </label>
            ) : (
              <label>
                Time of day (UTC)
                <div style={{ display: "flex", gap: 8 }}>
                  <input
                    type="number"
                    min={0}
                    max={23}
                    value={runAtHour}
                    onChange={(e) => setRunAtHour(e.target.value)}
                    style={{ width: 64 }}
                  />
                  <input
                    type="number"
                    min={0}
                    max={59}
                    value={runAtMinute}
                    onChange={(e) => setRunAtMinute(e.target.value)}
                    style={{ width: 64 }}
                  />
                </div>
              </label>
            )}
            <label>
              Devices
              <select value={scope} onChange={(e) => setScope(e.target.value as "all" | "specific")}>
                <option value="all">All devices (including any added later)</option>
                <option value="specific">Specific devices</option>
              </select>
            </label>
          </div>

          {scope === "specific" && (
            <div className="command-profile-card" style={{ marginTop: 12 }}>
              {devices.length === 0 ? (
                <p className="empty-state">No devices yet.</p>
              ) : (
                devices.map((d) => (
                  <label key={d.id} className="checkbox-label" style={{ flexDirection: "row", gap: 8 }}>
                    <input
                      type="checkbox"
                      checked={selectedDeviceIds.has(d.id)}
                      onChange={() => toggleDevice(d.id)}
                    />
                    {d.name}
                  </label>
                ))
              )}
            </div>
          )}

          <button type="submit" disabled={submitting} style={{ marginTop: 12 }}>
            {submitting ? "Saving…" : editingId ? "Save changes" : "Save schedule"}
          </button>
        </form>
      )}

      {loading ? (
        <p>Loading…</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Devices</th>
              <th>Frequency</th>
              <th>Next run (UTC)</th>
              <th>Last run</th>
              <th>Enabled</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {schedules.map((s) => (
              <tr key={s.id}>
                <td>{s.name}</td>
                <td>{describeDevices(s, deviceCount, deviceNameById)}</td>
                <td>{describeFrequency(s)}</td>
                <td>{new Date(s.next_run_at).toLocaleString()}</td>
                <td>
                  {s.last_run_at ? (
                    s.last_job_id ? (
                      <button className="link-button" onClick={() => navigate(`/jobs/${s.last_job_id}`)}>
                        {new Date(s.last_run_at).toLocaleString()}
                      </button>
                    ) : (
                      new Date(s.last_run_at).toLocaleString()
                    )
                  ) : (
                    "—"
                  )}
                </td>
                <td>
                  <input type="checkbox" checked={s.enabled} onChange={() => handleToggleEnabled(s)} />
                </td>
                <td>
                  <button className="link-button" onClick={() => handleEdit(s)}>
                    Edit
                  </button>
                  <button className="link-button" style={{ marginLeft: 12 }} onClick={() => handleRunNow(s.id)}>
                    Run now
                  </button>
                  <button
                    className="link-button danger"
                    style={{ marginLeft: 12 }}
                    onClick={() => handleDelete(s.id)}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {schedules.length === 0 && (
              <tr>
                <td colSpan={7} className="empty-state">
                  No schedules yet. Add one to collect configs automatically on a timer.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
