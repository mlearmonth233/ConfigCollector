import { useEffect, useState, type FormEvent } from "react";

import { extractErrorMessage } from "../api/client";
import { credentialsApi } from "../api/resources";
import type { Credential, MfaMode } from "../api/types";

const MFA_LABELS: Record<MfaMode, string> = {
  none: "None (plain username/password)",
  push: "Push approval (e.g. Duo push via TACACS+/RADIUS)",
  passcode: "One-time passcode (entered fresh for each collection run)",
};

export function Credentials() {
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [enableSecret, setEnableSecret] = useState("");
  const [mfaMode, setMfaMode] = useState<MfaMode>("none");
  const [otpDelimiter, setOtpDelimiter] = useState(",");
  const [authTimeoutSeconds, setAuthTimeoutSeconds] = useState("45");
  const [fallbackCredentialId, setFallbackCredentialId] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // A fallback can't itself have a fallback (no chains) - matches the
  // backend's validation, so only offer credentials that qualify.
  const eligibleFallbacks = credentials.filter((c) => !c.fallback_credential_id);

  async function refresh() {
    setLoading(true);
    try {
      const { data } = await credentialsApi.list();
      setCredentials(data);
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
    setName("");
    setUsername("");
    setPassword("");
    setEnableSecret("");
    setMfaMode("none");
    setOtpDelimiter(",");
    setAuthTimeoutSeconds("45");
    setFallbackCredentialId("");
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await credentialsApi.create({
        name,
        username,
        password,
        enable_secret: enableSecret || undefined,
        mfa_mode: mfaMode,
        otp_delimiter: otpDelimiter || ",",
        auth_timeout_seconds: Number(authTimeoutSeconds) || 45,
        fallback_credential_id: fallbackCredentialId || undefined,
      });
      resetForm();
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this credential? Devices using it will keep the reference until reassigned.")) return;
    try {
      await credentialsApi.remove(id);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <div className="page">
      <h1>Credentials</h1>
      <p className="page-subtitle">
        Reusable username/password sets for logging into devices. Secrets are encrypted at rest and
        never shown again after creation. TACACS+/RADIUS AAA (and any MFA on top of it) is configured
        on the device itself - here you just tell ConfigCollector how to log in and how long to wait.
      </p>
      {error && <div className="error-banner">{error}</div>}

      <form className="card-form" onSubmit={handleCreate}>
        <div className="form-grid">
          <label>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} required />
          </label>
          <label>
            Username
            <input value={username} onChange={(e) => setUsername(e.target.value)} required />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <label>
            Enable secret (optional)
            <input
              type="password"
              value={enableSecret}
              onChange={(e) => setEnableSecret(e.target.value)}
            />
          </label>
          <label>
            MFA / AAA mode
            <select value={mfaMode} onChange={(e) => setMfaMode(e.target.value as MfaMode)}>
              {Object.entries(MFA_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Auth timeout (seconds)
            <input
              type="number"
              min={5}
              max={300}
              value={authTimeoutSeconds}
              onChange={(e) => setAuthTimeoutSeconds(e.target.value)}
            />
            <span className="field-hint">
              How long to wait for login (SSH + TACACS+/RADIUS + any MFA approval) before giving up.
              Push-based MFA often needs 60-90s to give you time to approve on your phone.
            </span>
          </label>
          {mfaMode === "passcode" && (
            <label>
              Passcode delimiter
              <input value={otpDelimiter} onChange={(e) => setOtpDelimiter(e.target.value)} maxLength={8} />
              <span className="field-hint">
                Appended between the password and the one-time code you'll enter per collection run
                (e.g. "," produces "password,123456" - the convention most Duo/TACACS+ setups expect).
              </span>
            </label>
          )}
          <label>
            Fallback credential (optional)
            <select value={fallbackCredentialId} onChange={(e) => setFallbackCredentialId(e.target.value)}>
              <option value="">— none —</option>
              {eligibleFallbacks.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <span className="field-hint">
              Tried if this credential's login fails outright (e.g. TACACS+/RADIUS is unreachable or the
              account is locked) - typically a device's local/default account, or some other break-glass
              login. Only credentials with no fallback of their own can be picked (no chains).
            </span>
          </label>
        </div>
        <button type="submit" disabled={submitting}>
          {submitting ? "Adding…" : "Add credential"}
        </button>
      </form>

      {loading ? (
        <p>Loading…</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Username</th>
              <th>Enable secret</th>
              <th>MFA / AAA</th>
              <th>Auth timeout</th>
              <th>Fallback</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {credentials.map((c) => (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td>{c.username}</td>
                <td>{c.has_enable_secret ? "Set" : "—"}</td>
                <td>{c.mfa_mode === "none" ? "—" : c.mfa_mode}</td>
                <td>{c.auth_timeout_seconds}s</td>
                <td>{c.fallback_credential_name ?? "—"}</td>
                <td>
                  <button className="link-button danger" onClick={() => handleDelete(c.id)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {credentials.length === 0 && (
              <tr>
                <td colSpan={7} className="empty-state">
                  No credentials yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
