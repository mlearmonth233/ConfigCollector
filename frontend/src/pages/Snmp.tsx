import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { devicesApi, snmpApi, type SnmpProfilePayload } from "../api/resources";
import type { Device, SnmpAuthProtocol, SnmpJob, SnmpPrivProtocol, SnmpProfile, SnmpSecurityLevel, SnmpVersion } from "../api/types";
import { SnmpAlerting } from "../components/SnmpAlerting";
import { StatusBadge } from "../components/StatusBadge";
import { sortByDeviceName } from "../utils/deviceNameSort";

const AUTH_PROTOCOLS: SnmpAuthProtocol[] = ["SHA256", "SHA", "SHA224", "SHA384", "SHA512", "MD5"];
const PRIV_PROTOCOLS: SnmpPrivProtocol[] = ["AES128", "AES192", "AES256", "3DES", "DES"];

function describeProfile(p: SnmpProfile): string {
  if (p.version === "v2c") return "v2c · community";
  return `v3 · ${p.username ?? "?"} · ${p.security_level ?? "noAuthNoPriv"}` +
    (p.auth_protocol ? ` · ${p.auth_protocol}` : "") +
    (p.priv_protocol ? `/${p.priv_protocol}` : "");
}

function parseOids(text: string): string[] {
  return Array.from(new Set(text.split(/[\r\n,\s]+/).map((s) => s.trim().replace(/^\./, "")).filter(Boolean)));
}

export function Snmp() {
  const navigate = useNavigate();
  const [profiles, setProfiles] = useState<SnmpProfile[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [jobs, setJobs] = useState<SnmpJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // --- profile form ---
  const [showProfileForm, setShowProfileForm] = useState(false);
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null);
  const [pName, setPName] = useState("");
  const [pVersion, setPVersion] = useState<SnmpVersion>("v2c");
  const [pCommunity, setPCommunity] = useState("");
  const [pUsername, setPUsername] = useState("");
  const [pLevel, setPLevel] = useState<SnmpSecurityLevel>("authPriv");
  const [pAuthProto, setPAuthProto] = useState<SnmpAuthProtocol>("SHA256");
  const [pAuthPassword, setPAuthPassword] = useState("");
  const [pPrivProto, setPPrivProto] = useState<SnmpPrivProtocol>("AES128");
  const [pPrivPassword, setPPrivPassword] = useState("");
  const [pContext, setPContext] = useState("");
  const [pPort, setPPort] = useState("161");
  const [pTimeout, setPTimeout] = useState("3");
  const [pRetries, setPRetries] = useState("1");
  const [pDefault, setPDefault] = useState(false);
  const [savingProfile, setSavingProfile] = useState(false);

  // --- poll form ---
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [runProfileId, setRunProfileId] = useState("");
  const [extraOidsText, setExtraOidsText] = useState("");
  const [starting, setStarting] = useState(false);
  const [clearing, setClearing] = useState(false);

  const defaultProfile = useMemo(() => profiles.find((p) => p.is_default), [profiles]);
  const profileById = useMemo(() => new Map(profiles.map((p) => [p.id, p])), [profiles]);
  const sortedDevices = useMemo(() => sortByDeviceName(devices, (d) => d.name), [devices]);

  async function refresh() {
    setLoading(true);
    try {
      const [profilesRes, devicesRes, jobsRes] = await Promise.all([snmpApi.listProfiles(), devicesApi.list(), snmpApi.listJobs()]);
      setProfiles(profilesRes.data);
      setDevices(devicesRes.data);
      setJobs(jobsRes.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  function resetProfileForm() {
    setEditingProfileId(null);
    setPName("");
    setPVersion("v2c");
    setPCommunity("");
    setPUsername("");
    setPLevel("authPriv");
    setPAuthProto("SHA256");
    setPAuthPassword("");
    setPPrivProto("AES128");
    setPPrivPassword("");
    setPContext("");
    setPPort("161");
    setPTimeout("3");
    setPRetries("1");
    setPDefault(false);
  }

  function startEditProfile(p: SnmpProfile) {
    setEditingProfileId(p.id);
    setPName(p.name);
    setPVersion(p.version);
    setPCommunity("");
    setPUsername(p.username ?? "");
    setPLevel(p.security_level ?? "noAuthNoPriv");
    setPAuthProto(p.auth_protocol ?? "SHA256");
    setPAuthPassword("");
    setPPrivProto(p.priv_protocol ?? "AES128");
    setPPrivPassword("");
    setPContext(p.context_name ?? "");
    setPPort(String(p.port));
    setPTimeout(String(p.timeout_seconds));
    setPRetries(String(p.retries));
    setPDefault(p.is_default);
    setShowProfileForm(true);
  }

  async function handleSaveProfile(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const needsAuth = pVersion === "v3" && pLevel !== "noAuthNoPriv";
    const needsPriv = pVersion === "v3" && pLevel === "authPriv";
    if (!editingProfileId) {
      if (pVersion === "v2c" && !pCommunity) return setError("Enter the community string.");
      if (pVersion === "v3" && !pUsername) return setError("Enter the SNMPv3 username.");
      if (needsAuth && !pAuthPassword) return setError("Enter the authentication password.");
      if (needsPriv && !pPrivPassword) return setError("Enter the privacy (encryption) password.");
    }
    const payload: SnmpProfilePayload = {
      name: pName,
      version: pVersion,
      port: Number(pPort) || 161,
      timeout_seconds: Number(pTimeout) || 3,
      retries: Number(pRetries) || 0,
      is_default: pDefault,
    };
    if (pVersion === "v2c") {
      if (pCommunity) payload.community = pCommunity;
    } else {
      payload.username = pUsername;
      payload.security_level = pLevel;
      if (needsAuth) {
        payload.auth_protocol = pAuthProto;
        if (pAuthPassword) payload.auth_password = pAuthPassword;
      }
      if (needsPriv) {
        payload.priv_protocol = pPrivProto;
        if (pPrivPassword) payload.priv_password = pPrivPassword;
      }
      payload.context_name = pContext;
    }
    setSavingProfile(true);
    try {
      if (editingProfileId) {
        const { is_default: _ignored, ...rest } = payload;
        await snmpApi.updateProfile(editingProfileId, rest);
        if (pDefault) await snmpApi.setDefaultProfile(editingProfileId);
      } else {
        await snmpApi.createProfile(payload);
      }
      resetProfileForm();
      setShowProfileForm(false);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSavingProfile(false);
    }
  }

  async function handleDeleteProfile(p: SnmpProfile) {
    if (!confirm(`Delete SNMP profile "${p.name}"? Devices using it fall back to the org default.`)) return;
    setError(null);
    try {
      await snmpApi.removeProfile(p.id);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  async function handleSetDefault(p: SnmpProfile) {
    setError(null);
    try {
      await snmpApi.setDefaultProfile(p.id);
      await refresh();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  function toggleDevice(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleStartPoll() {
    setError(null);
    if (selectedIds.size === 0) return setError("Select at least one device to poll.");
    setStarting(true);
    try {
      const { data } = await snmpApi.createJob({
        device_ids: Array.from(selectedIds),
        snmp_profile_id: runProfileId || undefined,
        extra_oids: parseOids(extraOidsText),
      });
      navigate(`/snmp/jobs/${data.id}`);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setStarting(false);
    }
  }

  async function handleClearFinished() {
    if (!confirm("Clear all finished SNMP jobs? A job still in progress is left alone.")) return;
    setError(null);
    setClearing(true);
    try {
      const { data } = await snmpApi.clearFinished();
      await refresh();
      setNotice(`Cleared ${data.deleted} finished job(s).`);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setClearing(false);
    }
  }

  const needsAuth = pVersion === "v3" && pLevel !== "noAuthNoPriv";
  const needsPriv = pVersion === "v3" && pLevel === "authPriv";

  return (
    <div className="page">
      <h1>SNMP</h1>
      <p className="page-subtitle">
        Poll devices over SNMP for what they know about themselves: system details, every interface's
        status and error counters, and the device's own syslog history buffer (Cisco's CISCO-SYSLOG-MIB -
        the same messages you'd see with <code>show logging</code>), plus any extra OIDs you name. Store an
        SNMPv2c community or an SNMPv3 user once as a profile; secrets are encrypted and never shown again.
      </p>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="info-banner">{notice}</div>}

      {/* ---- profiles ---- */}
      <div className="page-header-row" style={{ marginTop: 8 }}>
        <h2 style={{ margin: 0 }}>SNMP profiles</h2>
        <div className="page-actions">
          <button
            onClick={() => {
              if (showProfileForm) resetProfileForm();
              setShowProfileForm((v) => !v);
            }}
          >
            {showProfileForm ? "Cancel" : "Add profile"}
          </button>
        </div>
      </div>

      {showProfileForm && (
        <form className="card-form" onSubmit={handleSaveProfile} style={{ marginTop: 12 }}>
          {editingProfileId && (
            <p className="field-hint" style={{ marginTop: 0 }}>
              Editing {pName || "profile"} - leave a password or community blank to keep the current one.
            </p>
          )}
          <div className="form-grid">
            <label>
              Name
              <input value={pName} onChange={(e) => setPName(e.target.value)} placeholder="e.g. netops read-only" required />
            </label>
            <label>
              Version
              <select value={pVersion} onChange={(e) => setPVersion(e.target.value as SnmpVersion)}>
                <option value="v2c">SNMPv2c (community string)</option>
                <option value="v3">SNMPv3 (user, auth, privacy)</option>
              </select>
            </label>

            {pVersion === "v2c" && (
              <label>
                Community string
                <input
                  type="password"
                  value={pCommunity}
                  onChange={(e) => setPCommunity(e.target.value)}
                  placeholder={editingProfileId ? "(unchanged)" : "e.g. public"}
                  autoComplete="off"
                />
              </label>
            )}

            {pVersion === "v3" && (
              <>
                <label>
                  Username
                  <input value={pUsername} onChange={(e) => setPUsername(e.target.value)} placeholder="e.g. snmp-netops" required />
                </label>
                <label>
                  Security level
                  <select value={pLevel} onChange={(e) => setPLevel(e.target.value as SnmpSecurityLevel)}>
                    <option value="authPriv">authPriv - authenticated and encrypted (recommended)</option>
                    <option value="authNoPriv">authNoPriv - authenticated only</option>
                    <option value="noAuthNoPriv">noAuthNoPriv - username only</option>
                  </select>
                </label>
                {needsAuth && (
                  <>
                    <label>
                      Authentication protocol
                      <select value={pAuthProto} onChange={(e) => setPAuthProto(e.target.value as SnmpAuthProtocol)}>
                        {AUTH_PROTOCOLS.map((p) => (
                          <option key={p} value={p}>
                            {p}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Authentication password
                      <input
                        type="password"
                        value={pAuthPassword}
                        onChange={(e) => setPAuthPassword(e.target.value)}
                        placeholder={editingProfileId ? "(unchanged)" : "at least 8 characters"}
                        autoComplete="new-password"
                      />
                    </label>
                  </>
                )}
                {needsPriv && (
                  <>
                    <label>
                      Privacy (encryption) protocol
                      <select value={pPrivProto} onChange={(e) => setPPrivProto(e.target.value as SnmpPrivProtocol)}>
                        {PRIV_PROTOCOLS.map((p) => (
                          <option key={p} value={p}>
                            {p}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Privacy password
                      <input
                        type="password"
                        value={pPrivPassword}
                        onChange={(e) => setPPrivPassword(e.target.value)}
                        placeholder={editingProfileId ? "(unchanged)" : "at least 8 characters"}
                        autoComplete="new-password"
                      />
                    </label>
                  </>
                )}
                <label>
                  Context name (optional)
                  <input value={pContext} onChange={(e) => setPContext(e.target.value)} placeholder="usually blank" />
                </label>
              </>
            )}

            <label>
              UDP port
              <input type="number" min={1} max={65535} value={pPort} onChange={(e) => setPPort(e.target.value)} />
            </label>
            <label>
              Timeout (seconds) / retries
              <div style={{ display: "flex", gap: 8 }}>
                <input type="number" min={1} max={60} value={pTimeout} onChange={(e) => setPTimeout(e.target.value)} style={{ width: 80 }} />
                <input type="number" min={0} max={5} value={pRetries} onChange={(e) => setPRetries(e.target.value)} style={{ width: 80 }} />
              </div>
            </label>
            <label className="checkbox-label" style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
              <input type="checkbox" checked={pDefault} onChange={(e) => setPDefault(e.target.checked)} />
              <span>Use as the org default (for devices with no SNMP profile of their own)</span>
            </label>
          </div>
          <button type="submit" disabled={savingProfile} style={{ marginTop: 12 }}>
            {savingProfile ? "Saving…" : editingProfileId ? "Save changes" : "Save profile"}
          </button>
        </form>
      )}

      {!loading && (
        <table className="data-table" style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Name</th>
              <th>Settings</th>
              <th>Port</th>
              <th>Devices</th>
              <th>Default</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {profiles.map((p) => (
              <tr key={p.id}>
                <td>{p.name}</td>
                <td>
                  <code>{describeProfile(p)}</code>
                </td>
                <td>{p.port}</td>
                <td>{p.device_count}</td>
                <td>
                  {p.is_default ? (
                    <span className="status-badge status-completed">Default</span>
                  ) : (
                    <button className="link-button" onClick={() => handleSetDefault(p)}>
                      Make default
                    </button>
                  )}
                </td>
                <td>
                  <button className="link-button" onClick={() => startEditProfile(p)}>
                    Edit
                  </button>
                  <button className="link-button danger" style={{ marginLeft: 12 }} onClick={() => handleDeleteProfile(p)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {profiles.length === 0 && (
              <tr>
                <td colSpan={6} className="empty-state">
                  No SNMP profiles yet. Add a v2c community or a v3 user to start polling.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {/* ---- start a poll ---- */}
      <h2 style={{ marginTop: 36 }}>Poll devices</h2>
      <div className="card-form" style={{ marginTop: 12 }}>
        <div className="form-grid">
          <label>
            Profile for this run
            <select value={runProfileId} onChange={(e) => setRunProfileId(e.target.value)}>
              <option value="">
                {defaultProfile
                  ? `Each device's own profile, else the default (${defaultProfile.name})`
                  : "Each device's own profile (no org default set)"}
              </option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} - {describeProfile(p)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Extra OIDs to walk (optional, numeric, one per line)
            <textarea
              rows={3}
              value={extraOidsText}
              onChange={(e) => setExtraOidsText(e.target.value)}
              placeholder={"1.3.6.1.4.1.9.9.109.1.1.1.1.7   (Cisco CPU 5-min)\n1.3.6.1.4.1.9.9.48.1.1.1.5      (Cisco memory free)"}
              style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
            />
          </label>
        </div>

        <h3 style={{ marginTop: 16 }}>Devices</h3>
        {sortedDevices.length === 0 ? (
          <p className="empty-state" style={{ padding: "10px 0" }}>
            No devices yet - add some on the <Link to="/devices">Devices</Link> page.
          </p>
        ) : (
          <>
            <div className="page-actions" style={{ marginBottom: 8 }}>
              <button className="link-button" onClick={() => setSelectedIds(new Set(sortedDevices.map((d) => d.id)))}>
                Select all
              </button>
              <button className="link-button" onClick={() => setSelectedIds(new Set())}>
                Clear
              </button>
            </div>
            <div className="command-profile-card" style={{ maxHeight: 240, overflowY: "auto" }}>
              {sortedDevices.map((d) => (
                <label key={d.id} className="checkbox-label" style={{ flexDirection: "row", gap: 8 }}>
                  <input type="checkbox" checked={selectedIds.has(d.id)} onChange={() => toggleDevice(d.id)} />
                  <span>
                    {d.name} <span className="field-hint">({d.host})</span>
                    {d.snmp_profile_id && profileById.get(d.snmp_profile_id) && (
                      <span className="field-hint"> · {profileById.get(d.snmp_profile_id)!.name}</span>
                    )}
                  </span>
                </label>
              ))}
            </div>
          </>
        )}
        <button onClick={handleStartPoll} disabled={starting || selectedIds.size === 0} style={{ marginTop: 12 }}>
          {starting ? "Starting…" : `Poll ${selectedIds.size} device${selectedIds.size === 1 ? "" : "s"}`}
        </button>
      </div>

      {/* ---- alerting ---- */}
      <SnmpAlerting devices={devices} profiles={profiles} />

      {/* ---- past jobs ---- */}
      <div className="page-header-row" style={{ marginTop: 36 }}>
        <h2 style={{ marginBottom: 0 }}>Past polls</h2>
        {jobs.length > 0 && (
          <button className="link-button danger" disabled={clearing} onClick={handleClearFinished}>
            {clearing ? "Clearing…" : "Clear all finished jobs"}
          </button>
        )}
      </div>
      {loading ? (
        <p>Loading…</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Started</th>
              <th>Status</th>
              <th>Devices</th>
              <th>Profile</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>{new Date(j.created_at).toLocaleString()}</td>
                <td>
                  <StatusBadge status={j.status} />
                </td>
                <td>{j.item_count}</td>
                <td>{j.snmp_profile_id ? profileById.get(j.snmp_profile_id)?.name ?? "(deleted profile)" : "per device / default"}</td>
                <td>
                  <Link to={`/snmp/jobs/${j.id}`}>View</Link>
                </td>
              </tr>
            ))}
            {jobs.length === 0 && (
              <tr>
                <td colSpan={5} className="empty-state">
                  No SNMP polls yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
