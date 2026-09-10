import { useEffect, useState, type FormEvent } from "react";

import { extractErrorMessage } from "../api/client";
import { organizationApi } from "../api/resources";
import type { OrganizationSettings } from "../api/types";

export function Settings() {
  const [settings, setSettings] = useState<OrganizationSettings | null>(null);
  const [retentionDays, setRetentionDays] = useState("");
  const [keepForever, setKeepForever] = useState(true);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    organizationApi
      .get()
      .then(({ data }) => {
        setSettings(data);
        if (data.snapshot_retention_days !== null) {
          setKeepForever(false);
          setRetentionDays(String(data.snapshot_retention_days));
        }
      })
      .catch((err) => setError(extractErrorMessage(err)))
      .finally(() => setLoading(false));
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    setSaving(true);
    try {
      const { data } = keepForever
        ? await organizationApi.update({ clear_retention: true })
        : await organizationApi.update({ snapshot_retention_days: Number(retentionDays) || 1 });
      setSettings(data);
      setSaved(true);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="page">
      <h1>Settings</h1>
      {error && <div className="error-banner">{error}</div>}
      {loading ? (
        <p>Loading…</p>
      ) : (
        <form className="card-form" onSubmit={handleSubmit}>
          <h3 style={{ marginTop: 0 }}>Snapshot retention</h3>
          <p className="page-subtitle" style={{ marginTop: 0 }}>
            How long to keep a device's collected configs before they're automatically deleted. Applies
            to {settings?.name}.
          </p>
          <label className="inline-label" style={{ marginBottom: 10 }}>
            <input
              type="checkbox"
              checked={keepForever}
              onChange={(e) => setKeepForever(e.target.checked)}
            />
            Keep forever (never auto-delete)
          </label>
          {!keepForever && (
            <label style={{ maxWidth: 200 }}>
              Delete configs older than (days)
              <input
                type="number"
                min={1}
                value={retentionDays}
                onChange={(e) => setRetentionDays(e.target.value)}
                required
              />
            </label>
          )}
          <div className="page-header-row" style={{ marginTop: 14 }}>
            {saved && <p className="page-subtitle" style={{ margin: 0 }}>Saved.</p>}
            <button type="submit" disabled={saving} style={{ marginLeft: "auto" }}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
