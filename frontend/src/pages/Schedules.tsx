import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { devicesApi, schedulesApi, type ScheduleTimingPayload } from "../api/resources";
import type { Device, Schedule, ScheduleFrequency } from "../api/types";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const FREQUENCY_OPTIONS: { value: ScheduleFrequency; label: string }[] = [
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "once", label: "One time (pick a date and time)" },
  { value: "every_n_hours", label: "Every N hours" },
];

const BROWSER_TIMEZONE = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
})();

function pad2(n: number | null | undefined): string {
  return String(n ?? 0).padStart(2, "0");
}

function describeFrequency(s: Schedule): string {
  const tz = s.timezone ?? "UTC";
  const at = `${pad2(s.run_at_hour)}:${pad2(s.run_at_minute)} (${tz})`;
  switch (s.frequency) {
    case "once":
      return `One time - ${s.run_once_at ? new Date(s.run_once_at).toLocaleString() : "?"}`;
    case "every_n_hours":
      return `Every ${s.interval_hours} hour${s.interval_hours === 1 ? "" : "s"}`;
    case "daily":
      return `Daily at ${at}`;
    case "weekly":
      return `Weekly on ${WEEKDAYS[s.day_of_week ?? 0]} at ${at}`;
    case "monthly":
      return `Monthly on day ${s.day_of_month}${(s.day_of_month ?? 0) >= 29 ? " (or last day)" : ""} at ${at}`;
  }
}

function describeDevices(s: Schedule, deviceCount: number, deviceNameById: Map<string, string>): string {
  if (s.device_ids === null) return `All devices (${deviceCount})`;
  const names = s.device_ids.map((id) => deviceNameById.get(id) ?? "deleted device");
  return names.join(", ") || "0 devices";
}

function isFinishedOneTime(s: Schedule): boolean {
  return s.frequency === "once" && !s.enabled && new Date(s.next_run_at).getTime() <= Date.now();
}

/** A local "YYYY-MM-DDTHH:MM" string (what <input type="datetime-local"> wants) for a Date. */
function toDatetimeLocal(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

function defaultRunOnceAt(): string {
  const d = new Date(Date.now() + 60 * 60 * 1000);
  d.setSeconds(0, 0);
  return toDatetimeLocal(d);
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
  const [timeOfDay, setTimeOfDay] = useState("02:00");
  const [dayOfWeek, setDayOfWeek] = useState("0");
  const [dayOfMonth, setDayOfMonth] = useState("1");
  const [runOnceAt, setRunOnceAt] = useState(defaultRunOnceAt);
  const [timezone, setTimezone] = useState(BROWSER_TIMEZONE);
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
    setTimeOfDay("02:00");
    setDayOfWeek("0");
    setDayOfMonth("1");
    setRunOnceAt(defaultRunOnceAt());
    setTimezone(BROWSER_TIMEZONE);
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
    setTimeOfDay(`${pad2(s.run_at_hour ?? 2)}:${pad2(s.run_at_minute ?? 0)}`);
    setDayOfWeek(String(s.day_of_week ?? 0));
    setDayOfMonth(String(s.day_of_month ?? 1));
    setRunOnceAt(s.run_once_at ? toDatetimeLocal(new Date(s.run_once_at)) : defaultRunOnceAt());
    setTimezone(s.timezone ?? BROWSER_TIMEZONE);
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

  function buildTiming(): ScheduleTimingPayload {
    const [hh, mm] = timeOfDay.split(":").map((x) => Number(x));
    switch (frequency) {
      case "once":
        return { frequency, run_once_at: new Date(runOnceAt).toISOString() };
      case "every_n_hours":
        return { frequency, interval_hours: Number(intervalHours) || 1 };
      case "daily":
        return { frequency, run_at_hour: hh, run_at_minute: mm, timezone };
      case "weekly":
        return { frequency, run_at_hour: hh, run_at_minute: mm, day_of_week: Number(dayOfWeek), timezone };
      case "monthly":
        return { frequency, run_at_hour: hh, run_at_minute: mm, day_of_month: Number(dayOfMonth), timezone };
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (frequency === "once" && (!runOnceAt || new Date(runOnceAt).getTime() <= Date.now())) {
      setError("Pick a date and time in the future for a one-time run.");
      return;
    }
    if (scope === "specific" && selectedDeviceIds.size === 0) {
      setError("Select at least one device, or switch to all devices.");
      return;
    }
    setSubmitting(true);
    try {
      const timing = buildTiming();
      const deviceIds = scope === "specific" ? Array.from(selectedDeviceIds) : undefined;
      if (editingId) {
        await schedulesApi.update(editingId, {
          name,
          ...timing,
          device_ids: deviceIds,
          clear_device_ids: scope === "all",
        });
      } else {
        await schedulesApi.create({ name, ...timing, device_ids: deviceIds });
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

  const usesTimeOfDay = frequency === "daily" || frequency === "weekly" || frequency === "monthly";

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
        Automatic config backups - daily, weekly, monthly, at one specific date and time, or every few
        hours - across every device in the org (or a chosen set). Each run creates an ordinary job on
        the Jobs page and stores a snapshot per device. Runs are unattended, so a device whose
        credential needs a one-time passcode will fail on scheduled runs (use "Run now" or a manual
        collection for those instead).
      </p>

      {showForm && (
        <form className="card-form" onSubmit={handleSubmit}>
          {editingId && <p className="field-hint" style={{ marginTop: 0 }}>Editing {name || "schedule"}</p>}
          <div className="form-grid">
            <label>
              Name
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Nightly backup"
                required
              />
            </label>
            <label>
              Repeat
              <select value={frequency} onChange={(e) => setFrequency(e.target.value as ScheduleFrequency)}>
                {FREQUENCY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>

            {frequency === "once" && (
              <label>
                Date and time ({BROWSER_TIMEZONE})
                <input
                  type="datetime-local"
                  value={runOnceAt}
                  min={toDatetimeLocal(new Date())}
                  onChange={(e) => setRunOnceAt(e.target.value)}
                  required
                />
              </label>
            )}

            {frequency === "every_n_hours" && (
              <label>
                Every how many hours
                <input
                  type="number"
                  min={1}
                  value={intervalHours}
                  onChange={(e) => setIntervalHours(e.target.value)}
                />
              </label>
            )}

            {frequency === "weekly" && (
              <label>
                Day of week
                <select value={dayOfWeek} onChange={(e) => setDayOfWeek(e.target.value)}>
                  {WEEKDAYS.map((d, i) => (
                    <option key={d} value={String(i)}>
                      {d}
                    </option>
                  ))}
                </select>
              </label>
            )}

            {frequency === "monthly" && (
              <label>
                Day of month
                <input
                  type="number"
                  min={1}
                  max={31}
                  value={dayOfMonth}
                  onChange={(e) => setDayOfMonth(e.target.value)}
                />
                <span className="field-hint">Months shorter than this run on their last day.</span>
              </label>
            )}

            {usesTimeOfDay && (
              <label>
                Time of day
                <input type="time" value={timeOfDay} onChange={(e) => setTimeOfDay(e.target.value)} required />
                <span className="field-hint">
                  In {timezone}
                  {timezone !== BROWSER_TIMEZONE && (
                    <>
                      {" · "}
                      <button type="button" className="link-button" onClick={() => setTimezone(BROWSER_TIMEZONE)}>
                        use {BROWSER_TIMEZONE}
                      </button>
                    </>
                  )}
                </span>
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
              <th>Repeat</th>
              <th>Next run</th>
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
                <td>
                  {isFinishedOneTime(s) ? (
                    <span className="field-hint">Done - ran {new Date(s.next_run_at).toLocaleString()}</span>
                  ) : s.enabled ? (
                    new Date(s.next_run_at).toLocaleString()
                  ) : (
                    <span className="field-hint">Paused</span>
                  )}
                </td>
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
                  {isFinishedOneTime(s) ? (
                    <span className="field-hint">Finished</span>
                  ) : (
                    <input type="checkbox" checked={s.enabled} onChange={() => handleToggleEnabled(s)} />
                  )}
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
                  No schedules yet. Add one to back up configs automatically - daily, weekly, monthly, or at
                  a specific date and time.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
