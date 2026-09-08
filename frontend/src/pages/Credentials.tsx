import { useEffect, useState, type FormEvent } from "react";

import { extractErrorMessage } from "../api/client";
import { credentialsApi } from "../api/resources";
import type { Credential } from "../api/types";

export function Credentials() {
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [enableSecret, setEnableSecret] = useState("");
  const [submitting, setSubmitting] = useState(false);

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
      });
      setName("");
      setUsername("");
      setPassword("");
      setEnableSecret("");
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
        never shown again after creation.
      </p>
      {error && <div className="error-banner">{error}</div>}

      <form className="inline-form" onSubmit={handleCreate}>
        <input placeholder="Name (e.g. lab-admin)" value={name} onChange={(e) => setName(e.target.value)} required />
        <input placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} required />
        <input
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <input
          type="password"
          placeholder="Enable secret (optional)"
          value={enableSecret}
          onChange={(e) => setEnableSecret(e.target.value)}
        />
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
              <th></th>
            </tr>
          </thead>
          <tbody>
            {credentials.map((c) => (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td>{c.username}</td>
                <td>{c.has_enable_secret ? "Set" : "—"}</td>
                <td>
                  <button className="link-button danger" onClick={() => handleDelete(c.id)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {credentials.length === 0 && (
              <tr>
                <td colSpan={4} className="empty-state">
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
