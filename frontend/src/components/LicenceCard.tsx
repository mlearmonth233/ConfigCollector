import { useState, type FormEvent } from "react";

import { extractErrorMessage } from "../api/client";
import { licenceApi } from "../api/resources";
import type { LicenceFeature } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { useLicence } from "../context/LicenceContext";

/** Settings > Licence: the tier in effect, what it allows, and where a
 * key is pasted. */
export function LicenceCard() {
  const { user } = useAuth();
  const { licence, refresh } = useLicence();
  const isAdmin = user?.role === "admin";
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function handleApply(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const { data } = await licenceApi.apply(key.trim());
      await refresh();
      setKey("");
      setNotice(`Licence applied: ${data.tier_label}${data.customer ? ` for ${data.customer}` : ""}${data.expires_at ? `, valid until ${data.expires_at}` : ", no expiry"}.`);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove() {
    if (!confirm("Remove the licence key? The organization drops back to the free tier immediately.")) return;
    setBusy(true);
    setError(null);
    try {
      await licenceApi.remove();
      await refresh();
      setNotice("Licence removed. This organization is on Nest (free).");
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!licence) return null;
  const features = Object.entries(licence.all_features) as [LicenceFeature, string][];

  return (
    <div className="card-form" id="licence">
      <div className="page-header-row" style={{ marginBottom: 6 }}>
        <h3 style={{ margin: 0 }}>Licence</h3>
        <span className={`status-badge ${licence.valid ? "status-completed" : "status-fallback"}`}>
          {licence.tier_label}
          {licence.valid ? "" : " (free)"}
        </span>
      </div>
      <p className="page-subtitle" style={{ marginTop: 0 }}>
        {licence.valid ? (
          <>
            {licence.customer ? `Licensed to ${licence.customer}. ` : ""}
            {licence.expires_at ? `Valid until ${licence.expires_at}.` : "No expiry."}
            {licence.key_id ? ` Key ${licence.key_id}.` : ""}
          </>
        ) : (
          <>
            The free tier: manual backups, history and diffs, the ping monitor, terminal, DNS check, SNMP polling and the inventory view.
            {licence.problem ? ` A key is present but does not count: ${licence.problem}` : ""}
          </>
        )}
      </p>
      <div className="form-grid" style={{ marginBottom: 10 }}>
        <div>
          <span className="field-hint" style={{ display: "block", color: "var(--text-muted)" }}>Devices</span>
          <strong>
            {licence.device_count}
            {licence.max_devices !== null ? ` of ${licence.max_devices}` : " (unlimited)"}
          </strong>
        </div>
        <div>
          <span className="field-hint" style={{ display: "block", color: "var(--text-muted)" }}>Users</span>
          <strong>
            {licence.user_count}
            {licence.max_users !== null ? ` of ${licence.max_users}` : " (unlimited)"}
          </strong>
        </div>
        <div>
          <span className="field-hint" style={{ display: "block", color: "var(--text-muted)" }}>Config history</span>
          <strong>{licence.max_retention_days !== null ? `up to ${licence.max_retention_days} days` : "unlimited"}</strong>
        </div>
      </div>
      <ul className="licence-features">
        {features.map(([featureKey, label]) => {
          const on = licence.features.includes(featureKey);
          return (
            <li key={featureKey} className={on ? "on" : "off"}>
              <span aria-hidden="true">{on ? "✓" : "–"}</span> {label}
              {!on && <span className="field-hint"> Colony and Warren</span>}
            </li>
          );
        })}
      </ul>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="info-banner">{notice}</div>}
      {isAdmin ? (
        <form onSubmit={handleApply} style={{ marginTop: 10 }}>
          <label>
            {licence.has_key ? "Replace the licence key" : "Licence key"}
            <textarea
              id="licence-key"
              rows={3}
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="PKR1.…"
              style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12 }}
              required
            />
            <span className="field-hint">The key arrives by email after checkout. Paste it exactly as sent.</span>
          </label>
          <div className="page-header-row" style={{ marginTop: 10 }}>
            <div className="page-actions">
              {licence.has_key && (
                <button type="button" className="link-button danger" onClick={handleRemove} disabled={busy}>
                  Remove key
                </button>
              )}
            </div>
            <button type="submit" disabled={busy || !key.trim()}>
              {busy ? "Applying…" : "Apply key"}
            </button>
          </div>
        </form>
      ) : (
        <p className="field-hint">Only an admin can apply a licence key.</p>
      )}
    </div>
  );
}
