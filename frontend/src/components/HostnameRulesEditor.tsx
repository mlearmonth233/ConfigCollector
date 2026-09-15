import { useEffect, useMemo, useState } from "react";

import { extractErrorMessage } from "../api/client";
import { deviceRolesApi, deviceTypesApi, hostnameRulesApi } from "../api/resources";
import type { DeviceDetection, DeviceRole, DeviceType, HostnameMatchMode, HostnameRule, NetworkZone } from "../api/types";
import { useAuth } from "../context/AuthContext";

const MODE_LABELS: Record<HostnameMatchMode, string> = {
  contains: "contains",
  prefix: "starts with",
  suffix: "ends with",
  regex: "matches regex",
};

const ZONE_LABELS: Record<NetworkZone, string> = { it: "IT", ot: "OT" };

const CUSTOM_ROLE = "__custom__";

function emptyRule(): HostnameRule {
  return { pattern: "", match_mode: "contains", device_role: null, role_label: null, network_zone: null, device_type: null };
}

/** The org's device-naming convention: an ordered list of "name matching X
 *  means role/zone/type Y" rules, with a live tester. Lives on Settings. */
export function HostnameRulesEditor() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [rules, setRules] = useState<HostnameRule[]>([]);
  const [usingBuiltin, setUsingBuiltin] = useState(true);
  const [roles, setRoles] = useState<DeviceRole[]>([]);
  const [deviceTypes, setDeviceTypes] = useState<DeviceType[]>([]);
  const [loading, setLoading] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const [testName, setTestName] = useState("");
  const [testResult, setTestResult] = useState<DeviceDetection | null>(null);

  const knownRoleKeys = useMemo(() => new Set(roles.map((r) => r.key)), [roles]);

  async function load() {
    setLoading(true);
    try {
      const [rulesRes, rolesRes, typesRes] = await Promise.all([hostnameRulesApi.get(), deviceRolesApi.list(), deviceTypesApi.list()]);
      setRules(rulesRes.data.rules.map(({ sort_order: _o, ...r }) => r));
      setUsingBuiltin(rulesRes.data.using_builtin);
      setRoles(rolesRes.data);
      setDeviceTypes(typesRes.data);
      setDirty(false);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  // Live test against the *draft* rules, debounced.
  useEffect(() => {
    const name = testName.trim();
    if (!name) {
      setTestResult(null);
      return;
    }
    const handle = setTimeout(async () => {
      try {
        const { data } = await hostnameRulesApi.test(name, rules.filter((r) => r.pattern.trim()));
        setTestResult(data);
      } catch {
        setTestResult(null);
      }
    }, 300);
    return () => clearTimeout(handle);
  }, [testName, rules]);

  function update(index: number, patch: Partial<HostnameRule>) {
    setRules((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
    setDirty(true);
    setSaved(false);
  }

  function move(index: number, delta: number) {
    setRules((prev) => {
      const next = [...prev];
      const target = index + delta;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    setDirty(true);
  }

  function remove(index: number) {
    setRules((prev) => prev.filter((_, i) => i !== index));
    setDirty(true);
  }

  async function save(rulesToSave: HostnameRule[]) {
    setError(null);
    setSaving(true);
    try {
      const cleaned = rulesToSave
        .filter((r) => r.pattern.trim())
        .map((r) => ({
          ...r,
          pattern: r.pattern.trim(),
          device_role: r.device_role?.trim() || null,
          role_label: r.role_label?.trim() || null,
          device_type: r.device_type || null,
        }));
      const { data } = await hostnameRulesApi.replace(cleaned);
      setRules(data.rules.map(({ sort_order: _o, ...r }) => r));
      setUsingBuiltin(data.using_builtin);
      setDirty(false);
      setSaved(true);
      const rolesRes = await deviceRolesApi.list();
      setRoles(rolesRes.data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function resetToBuiltin() {
    if (!confirm("Discard your naming rules and go back to the built-in convention (SWA/SWC/..., P0/O0)?")) return;
    await save([]);
  }

  if (loading) return <p>Loading…</p>;

  return (
    <div className="card-form" style={{ marginTop: 24 }}>
      <div className="page-header-row" style={{ marginBottom: 6 }}>
        <h3 style={{ margin: 0 }}>Device naming rules</h3>
        {usingBuiltin ? (
          <span className="status-badge status-fallback">using built-in convention</span>
        ) : (
          <span className="status-badge status-completed">custom convention</span>
        )}
      </div>
      <p className="page-subtitle" style={{ marginTop: 0 }}>
        How Packrat guesses a device's role, IT/OT zone and device type from its name when you add
        devices. Rules run top to bottom; for each of role, zone and type the first matching rule wins,
        so a role rule and a zone rule can both apply to one name. Everything guessed is only a pre-fill
        you can change. {usingBuiltin && "Edit these built-in rules to describe your own convention, and save."}
      </p>
      {error && <div className="error-banner">{error}</div>}

      <div style={{ overflowX: "auto" }}>
        <table className="data-table rules-table">
          <thead>
            <tr>
              <th style={{ width: 36 }}></th>
              <th>Match</th>
              <th>Pattern</th>
              <th>Role</th>
              <th>Zone</th>
              <th>Device type</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rules.map((r, i) => {
              const roleSelectValue = r.device_role === null ? "" : knownRoleKeys.has(r.device_role) ? r.device_role : CUSTOM_ROLE;
              return (
                <tr key={i}>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <button className="link-button" onClick={() => move(i, -1)} disabled={!isAdmin || i === 0} title="Move up" aria-label="Move up">
                      ↑
                    </button>
                    <button className="link-button" onClick={() => move(i, 1)} disabled={!isAdmin || i === rules.length - 1} title="Move down" aria-label="Move down">
                      ↓
                    </button>
                  </td>
                  <td>
                    <select value={r.match_mode} onChange={(e) => update(i, { match_mode: e.target.value as HostnameMatchMode })} disabled={!isAdmin}>
                      {(Object.keys(MODE_LABELS) as HostnameMatchMode[]).map((m) => (
                        <option key={m} value={m}>
                          {MODE_LABELS[m]}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      value={r.pattern}
                      onChange={(e) => update(i, { pattern: e.target.value })}
                      placeholder={r.match_mode === "regex" ? "e.g. ^den-.*-core" : "e.g. SWC or -core-"}
                      style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", minWidth: 140 }}
                      disabled={!isAdmin}
                    />
                  </td>
                  <td>
                    <select
                      value={roleSelectValue}
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v === CUSTOM_ROLE) update(i, { device_role: r.device_role && !knownRoleKeys.has(r.device_role) ? r.device_role : "my_role" });
                        else update(i, { device_role: v || null, role_label: null });
                      }}
                      disabled={!isAdmin}
                    >
                      <option value="">— no role —</option>
                      {roles.map((role) => (
                        <option key={role.key} value={role.key}>
                          {role.label}
                        </option>
                      ))}
                      <option value={CUSTOM_ROLE}>Custom role…</option>
                    </select>
                    {roleSelectValue === CUSTOM_ROLE && (
                      <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                        <input
                          value={r.device_role ?? ""}
                          onChange={(e) => update(i, { device_role: e.target.value })}
                          placeholder="key, e.g. wan_edge"
                          style={{ width: 130, fontFamily: "ui-monospace, Menlo, monospace" }}
                          disabled={!isAdmin}
                        />
                        <input
                          value={r.role_label ?? ""}
                          onChange={(e) => update(i, { role_label: e.target.value })}
                          placeholder="label, e.g. WAN edge"
                          style={{ width: 140 }}
                          disabled={!isAdmin}
                        />
                      </div>
                    )}
                  </td>
                  <td>
                    <select
                      value={r.network_zone ?? ""}
                      onChange={(e) => update(i, { network_zone: (e.target.value || null) as NetworkZone | null })}
                      disabled={!isAdmin}
                    >
                      <option value="">— no zone —</option>
                      <option value="it">IT</option>
                      <option value="ot">OT</option>
                    </select>
                  </td>
                  <td>
                    <select value={r.device_type ?? ""} onChange={(e) => update(i, { device_type: e.target.value || null })} disabled={!isAdmin}>
                      <option value="">— don't guess —</option>
                      {deviceTypes.map((t) => (
                        <option key={t.key} value={t.key}>
                          {t.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <button className="link-button danger" onClick={() => remove(i)} disabled={!isAdmin}>
                      Remove
                    </button>
                  </td>
                </tr>
              );
            })}
            {rules.length === 0 && (
              <tr>
                <td colSpan={7} className="empty-state">
                  No rules. Saving an empty list means the built-in convention applies.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {isAdmin && (
        <div className="page-header-row" style={{ marginTop: 12 }}>
          <div className="page-actions">
            <button
              className="link-button"
              onClick={() => {
                setRules((prev) => [...prev, emptyRule()]);
                setDirty(true);
              }}
            >
              + Add rule
            </button>
            {!usingBuiltin && (
              <button className="link-button danger" onClick={resetToBuiltin} disabled={saving}>
                Reset to built-in
              </button>
            )}
          </div>
          <div className="page-actions">
            {saved && <span className="page-subtitle" style={{ margin: 0 }}>Saved.</span>}
            <button onClick={() => save(rules)} disabled={saving || !dirty}>
              {saving ? "Saving…" : "Save rules"}
            </button>
          </div>
        </div>
      )}

      <h4 style={{ margin: "20px 0 6px" }}>Try a hostname</h4>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <input
          value={testName}
          onChange={(e) => setTestName(e.target.value)}
          placeholder="e.g. den-core-sw01"
          style={{ maxWidth: 280, fontFamily: "ui-monospace, Menlo, monospace" }}
        />
        <span className="field-hint">
          {testName.trim() === ""
            ? "Type a name to see what the rules above (including unsaved edits) make of it."
            : testResult === null
              ? "…"
              : [
                  testResult.device_role_label ?? "no role",
                  testResult.network_zone ? ZONE_LABELS[testResult.network_zone] : "no zone",
                  testResult.suggested_device_type
                    ? deviceTypes.find((t) => t.key === testResult.suggested_device_type)?.label ?? testResult.suggested_device_type
                    : "no device type guess",
                ].join(" · ")}
        </span>
      </div>
    </div>
  );
}
