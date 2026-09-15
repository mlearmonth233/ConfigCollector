import { useEffect, useMemo, useState, type FormEvent } from "react";

import { extractErrorMessage } from "../api/client";
import { customDeviceTypesApi } from "../api/resources";
import type { CustomDeviceType } from "../api/types";

function slugify(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^[^a-z]+/, "")
    .slice(0, 64);
}

function parseCommands(raw: string): string[] {
  return raw
    .split(/[\n,]/)
    .map((c) => c.trim())
    .filter((c) => c.length > 0);
}

const CATEGORY_LABELS: Record<string, string> = {
  switch: "Switch",
  router: "Router",
  firewall: "Firewall",
  wlc: "Wireless controller",
  pdu: "PDU",
  server: "Server",
  console_server: "Console server",
  other: "Other",
};

/** Org-defined device types: any platform the built-in list doesn't cover,
 *  with a Netmiko driver and the commands to collect from it. Shown at the
 *  top of the Commands page; each type then appears in the Add-device
 *  dropdown under "Your device types". */
export function CustomDeviceTypesSection() {
  const [types, setTypes] = useState<CustomDeviceType[]>([]);
  const [drivers, setDrivers] = useState<string[]>([]);
  const [categories, setCategories] = useState<string[]>(Object.keys(CATEGORY_LABELS));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [key, setKey] = useState("");
  const [keyTouched, setKeyTouched] = useState(false);
  const [category, setCategory] = useState("other");
  const [driver, setDriver] = useState("");
  const [commands, setCommands] = useState("");
  const [timingRead, setTimingRead] = useState(true);
  const [secretSupported, setSecretSupported] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const commandList = useMemo(() => parseCommands(commands), [commands]);

  async function refresh() {
    setLoading(true);
    try {
      const [typesRes, driversRes] = await Promise.all([customDeviceTypesApi.list(), customDeviceTypesApi.drivers()]);
      setTypes(typesRes.data);
      setDrivers(driversRes.data.drivers);
      setCategories(driversRes.data.categories);
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
    setLabel("");
    setKey("");
    setKeyTouched(false);
    setCategory("other");
    setDriver("");
    setCommands("");
    setTimingRead(true);
    setSecretSupported(false);
  }

  function startEdit(t: CustomDeviceType) {
    setEditingId(t.id);
    setLabel(t.label);
    setKey(t.key);
    setKeyTouched(true);
    setCategory(t.category);
    setDriver(t.netmiko_driver);
    setCommands(t.default_commands.join("\n"));
    setTimingRead(t.timing_read);
    setSecretSupported(t.secret_supported);
    setShowForm(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!driver.trim()) {
      setError("Pick the Netmiko driver that speaks this platform's CLI.");
      return;
    }
    if (!drivers.includes(driver.trim())) {
      setError(`"${driver}" is not a Netmiko driver name - pick one from the list.`);
      return;
    }
    if (commandList.length === 0) {
      setError("Enter at least one command to run - e.g. show logging, or show configuration.");
      return;
    }
    setSubmitting(true);
    try {
      if (editingId) {
        await customDeviceTypesApi.update(editingId, {
          label: label.trim(),
          category,
          netmiko_driver: driver.trim(),
          default_commands: commandList,
          secret_supported: secretSupported,
          timing_read: timingRead,
        });
      } else {
        await customDeviceTypesApi.create({
          key: key.trim(),
          label: label.trim(),
          category,
          netmiko_driver: driver.trim(),
          default_commands: commandList,
          secret_supported: secretSupported,
          timing_read: timingRead,
        });
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

  async function handleDelete(t: CustomDeviceType) {
    if (!confirm(`Delete the device type "${t.label}"? Devices can't use it afterwards.`)) return;
    setError(null);
    try {
      await customDeviceTypesApi.remove(t.id);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <section style={{ marginBottom: 32 }}>
      <div className="page-header-row">
        <h2 className="category-heading" style={{ margin: 0, textTransform: "none" }}>
          Your device types
        </h2>
        <div className="page-actions">
          <button
            onClick={() => {
              if (showForm) resetForm();
              setShowForm((v) => !v);
            }}
          >
            {showForm ? "Cancel" : "Add device type"}
          </button>
        </div>
      </div>
      <p className="page-subtitle" style={{ marginTop: 6 }}>
        Anything the built-in list doesn't cover - Juniper, Arista, Palo Alto, a Linux syslog box, a
        vendor's console server. Pick the Netmiko driver that speaks its CLI and list the commands to
        collect (configs, logs, inventory - whatever you want kept). It then shows up in the Add-device
        dropdown like any other type.
      </p>
      {error && <div className="error-banner">{error}</div>}

      {showForm && (
        <form className="card-form" onSubmit={handleSubmit} style={{ marginBottom: 16 }}>
          <div className="form-grid">
            <label>
              Name
              <input
                value={label}
                onChange={(e) => {
                  setLabel(e.target.value);
                  if (!keyTouched && !editingId) setKey(slugify(e.target.value));
                }}
                placeholder="e.g. Juniper SRX firewall"
                required
              />
            </label>
            <label>
              Key (stored on devices, can't change later)
              <input
                value={key}
                onChange={(e) => {
                  setKeyTouched(true);
                  setKey(e.target.value);
                }}
                placeholder="e.g. juniper_srx"
                pattern="[a-z][a-z0-9_]{1,63}"
                title="Lowercase letters, digits and underscores, starting with a letter"
                disabled={!!editingId}
                required
              />
            </label>
            <label>
              Netmiko driver
              <input
                list="netmiko-drivers"
                value={driver}
                onChange={(e) => setDriver(e.target.value)}
                placeholder="start typing: juniper, arista_eos, paloalto_panos, linux..."
                required
              />
              <datalist id="netmiko-drivers">
                {drivers.map((d) => (
                  <option key={d} value={d} />
                ))}
              </datalist>
              <span className="field-hint">
                The vendor CLI dialect Netmiko should speak. Unsure? <code>linux</code> for anything with a
                bash-like shell, <code>generic_termserver</code> for a plain prompt.
              </span>
            </label>
            <label>
              Category
              <select value={category} onChange={(e) => setCategory(e.target.value)}>
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {CATEGORY_LABELS[c] ?? c}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label style={{ marginTop: 12 }}>
            Commands to run (one per line or comma-separated)
            <textarea
              value={commands}
              onChange={(e) => setCommands(e.target.value)}
              rows={5}
              placeholder={"show configuration | display set\nshow log messages | last 200\nshow system alarms"}
              style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" }}
            />
            <span className="field-hint">
              {commandList.length > 0
                ? `Runs ${commandList.length} command${commandList.length === 1 ? "" : "s"} in this order. A device's own custom command list and a one-time override at collection still take precedence.`
                : "Include the platform's paging-off command first if it has one (e.g. \"set cli screen-length 0\" on Junos, \"terminal length 0\" on Arista)."}
            </span>
          </label>

          <div className="form-grid" style={{ marginTop: 12 }}>
            <label className="checkbox-label" style={{ flexDirection: "row", gap: 8, alignItems: "flex-start" }}>
              <input type="checkbox" checked={timingRead} onChange={(e) => setTimingRead(e.target.checked)} />
              <span>
                Read output by waiting for the channel to go quiet (recommended for a platform not yet proven
                here - untick only if you know Netmiko's prompt detection works for this driver)
              </span>
            </label>
            <label className="checkbox-label" style={{ flexDirection: "row", gap: 8, alignItems: "flex-start" }}>
              <input type="checkbox" checked={secretSupported} onChange={(e) => setSecretSupported(e.target.checked)} />
              <span>Has an "enable" privileged mode (send the credential's enable secret after login)</span>
            </label>
          </div>

          <button type="submit" disabled={submitting} style={{ marginTop: 12 }}>
            {submitting ? "Saving…" : editingId ? "Save changes" : "Add device type"}
          </button>
        </form>
      )}

      {loading ? (
        <p>Loading…</p>
      ) : types.length === 0 ? (
        <p className="empty-state" style={{ padding: "14px 0" }}>
          No custom device types yet. Add one to collect from a platform that isn't in the built-in list.
        </p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Key</th>
              <th>Driver</th>
              <th>Category</th>
              <th>Commands</th>
              <th>Devices</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {types.map((t) => (
              <tr key={t.id}>
                <td>{t.label}</td>
                <td>
                  <code>{t.key}</code>
                </td>
                <td>
                  <code>{t.netmiko_driver}</code>
                  <div className="field-hint">
                    {t.timing_read ? "timing read" : "prompt read"}
                    {t.secret_supported ? " · enable" : ""}
                  </div>
                </td>
                <td>{CATEGORY_LABELS[t.category] ?? t.category}</td>
                <td style={{ maxWidth: 360 }}>
                  <code style={{ whiteSpace: "normal" }}>{t.default_commands.join(", ")}</code>
                </td>
                <td>{t.device_count}</td>
                <td>
                  <button className="link-button" onClick={() => startEdit(t)}>
                    Edit
                  </button>
                  <button
                    className="link-button danger"
                    style={{ marginLeft: 12 }}
                    onClick={() => handleDelete(t)}
                    disabled={t.device_count > 0}
                    title={t.device_count > 0 ? "Change or delete the devices using this type first" : undefined}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
