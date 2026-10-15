import { Fragment, useEffect, useRef, useState } from "react";

import { extractErrorMessage } from "../api/client";
import type { Device } from "../api/types";
import type { WirelessStandard, WirelessStandardResponse } from "../api/wirelessAudit";
import { wirelessAuditApi } from "../api/wirelessAudit";

type ListKey = {
  [K in keyof WirelessStandard]: WirelessStandard[K] extends string[] ? K : never;
}[keyof WirelessStandard];
type NumberKey = {
  [K in keyof WirelessStandard]: WirelessStandard[K] extends number ? K : never;
}[keyof WirelessStandard];
type BoolKey = {
  [K in keyof WirelessStandard]: WirelessStandard[K] extends boolean ? K : never;
}[keyof WirelessStandard];
type TextKey = "ap_name_pattern" | "dns_domain" | "ap_group_name_pattern";

const LISTS: { key: ListKey; label: string; hint: string }[] = [
  { key: "ap_subnets", label: "AP management subnets", hint: "CIDR, one per line: 10.20.0.0/22" },
  { key: "approved_ap_models", label: "Approved AP models", hint: "Model prefixes: C9130AXI, C9120AXI" },
  { key: "country_codes", label: "Country codes", hint: "US, GB…" },
  { key: "approved_9800_versions", label: "Approved 9800 releases", hint: "Prefixes: 17.9.6, 17.12" },
  { key: "approved_aireos_versions", label: "Approved AireOS releases", hint: "8.10.196" },
  { key: "ntp_servers", label: "NTP servers", hint: "Must be configured on every controller" },
  { key: "syslog_servers", label: "Syslog servers", hint: "" },
  { key: "dns_servers", label: "DNS servers", hint: "" },
  { key: "required_ssids", label: "Required SSIDs", hint: "Configured and enabled on every controller" },
  { key: "open_ssids_allowed", label: "Open SSIDs allowed", hint: "Guest SSIDs allowed without layer-2 security" },
  { key: "legacy_ap_models", label: "802.11ac Wave 2 (legacy) AP models", hint: "Kept out of 802.11ax AP groups / site tags" },
  { key: "log_ignore_patterns", label: "Log lines to ignore", hint: "Regular expressions" },
];
const NUMBERS: { key: NumberKey; label: string }[] = [
  { key: "min_ap_uplink_mbps", label: "Minimum AP uplink (Mbps)" },
  { key: "max_clients_per_ap", label: "Max clients per AP" },
  { key: "client_rssi_warn", label: "Client RSSI warning (dBm)" },
  { key: "client_rssi_fail", label: "Client RSSI failure (dBm)" },
  { key: "client_snr_warn", label: "Client SNR warning (dB)" },
  { key: "client_snr_fail", label: "Client SNR failure (dB)" },
  { key: "max_cpu_percent", label: "Controller CPU warning (%)" },
  { key: "max_wncd_cpu_percent", label: "WNCD CPU warning (%)" },
  { key: "wncd_imbalance_ratio", label: "WNCD imbalance (× average APs)" },
  { key: "max_aps_per_flex_site_tag", label: "Max APs per FlexConnect site tag" },
  { key: "max_snapshot_age_hours", label: "Snapshot is stale after (hours)" },
];
const BOOLS: { key: BoolKey; label: string }[] = [
  { key: "check_dns", label: "Look AP names up in DNS" },
  { key: "check_reverse_dns", label: "Check AP PTR records" },
  { key: "require_ha", label: "Require an SSO hot-standby peer" },
  { key: "require_full_duplex", label: "AP uplinks full duplex" },
  { key: "require_primary_controller", label: "APs primed with a primary controller (9800)" },
  { key: "require_non_default_tags", label: "No AP on default tags (9800)" },
  { key: "flag_default_ap_group", label: "No AP in default-group / default-site-tag" },
  { key: "flag_mixed_ap_generations", label: "Keep 802.11ac and 802.11ax APs in separate groups" },
  { key: "flag_disabled_radios", label: "Flag disabled radios" },
  { key: "security_checks", label: "Management-plane security checks" },
  { key: "flag_unexpected_ssids", label: "Flag SSIDs not in the required list" },
  { key: "flag_legacy_clients", label: "Flag legacy 802.11a/b/g clients" },
];

function toLines(values: (string | number)[]): string {
  return values.join("\n");
}

function fromLines(text: string): string[] {
  return text
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** The wireless standard: workbook imports (configuration templates and
 * the Return-to-Operations checklist), which profile/checklist applies to
 * which controller, and the audit's own thresholds. */
export function WirelessStandardEditor({ isAdmin, controllers, onSaved }: { isAdmin: boolean; controllers: Device[]; onSaved: () => void }) {
  const [data, setData] = useState<WirelessStandardResponse | null>(null);
  const [std, setStd] = useState<WirelessStandard | null>(null);
  const [lists, setLists] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [importing, setImporting] = useState<"standard" | "r2o" | null>(null);
  const [openProfile, setOpenProfile] = useState<string | null>(null);
  const [openChecklist, setOpenChecklist] = useState<string | null>(null);
  const standardInput = useRef<HTMLInputElement>(null);
  const r2oInput = useRef<HTMLInputElement>(null);

  function adopt(response: WirelessStandardResponse) {
    setData(response);
    setStd(response.standard);
    const l: Record<string, string> = {};
    for (const { key } of LISTS) l[key] = toLines(response.standard[key]);
    l.allowed_24ghz_channels = toLines(response.standard.allowed_24ghz_channels);
    l.template_variables = Object.entries(response.standard.template_variables)
      .map(([k, v]) => `${k}=${v}`)
      .join("\n");
    setLists(l);
  }

  async function load() {
    try {
      adopt((await wirelessAuditApi.getStandard()).data);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  if (!std || !data) return error ? <div className="error-banner">{error}</div> : <p>Loading the standard…</p>;
  const current = std;

  function set<K extends keyof WirelessStandard>(key: K, value: WirelessStandard[K]) {
    setStd({ ...current, [key]: value });
  }

  async function save() {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const payload: WirelessStandard = { ...current };
      for (const { key } of LISTS) (payload[key] as string[]) = fromLines(lists[key] ?? "");
      payload.allowed_24ghz_channels = fromLines(lists.allowed_24ghz_channels ?? "").map(Number).filter((n) => !Number.isNaN(n));
      payload.template_variables = Object.fromEntries(
        fromLines(lists.template_variables ?? "")
          .map((line) => line.split("="))
          .filter((parts) => parts.length >= 2)
          .map(([k, ...v]) => [k.trim(), v.join("=").trim()]),
      );
      adopt((await wirelessAuditApi.saveStandard(payload)).data);
      setNotice("Standard saved.");
      onSaved();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleImport(kind: "standard" | "r2o", file: File | undefined) {
    if (!file) return;
    setImporting(kind);
    setError(null);
    setNotice(null);
    try {
      if (kind === "standard") {
        const { data: result } = await wirelessAuditApi.importStandard(file);
        adopt(result.standard);
        setNotice(
          `${result.filename}: ${result.profiles.map((p) => `${p.name} (${p.rules} lines, ${p.optional} optional, ${p.skipped_count} skipped)`).join("; ")}.` +
            (result.replaced.length ? ` Replaced the previous ${result.replaced.join(", ")}.` : ""),
        );
      } else {
        const { data: result } = await wirelessAuditApi.importR2O(file);
        await load();
        setNotice(
          `${result.filename}: ${result.checklists.map((c) => `${c.name} (${c.items.length} checks)`).join("; ")}.` +
            (result.replaced.length ? ` Replaced the previous ${result.replaced.join(", ")}.` : "") +
            " Add the checklist's commands on the Coverage tab, then collect the controllers.",
        );
      }
      onSaved();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setImporting(null);
      if (standardInput.current) standardInput.current.value = "";
      if (r2oInput.current) r2oInput.current.value = "";
    }
  }

  const ro = !isAdmin;
  const profileNames = current.template_profiles.map((p) => p.name);
  const checklistNames = current.r2o_checklists.map((c) => c.name);

  return (
    <div style={{ marginTop: 12 }}>
      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="info-banner">{notice}</div>}
      {ro && <div className="info-banner">Only an admin can change the standard.</div>}

      <div className="card-form">
        <h2 style={{ marginTop: 0 }}>Configuration standard workbook</h2>
        <p className="page-subtitle">
          The engineering standard as an Excel workbook: one sheet per controller build (e.g. "Flex vWLC 9800 IOS-XE", "WLC 9800 IOS-XE",
          "WLC AireOS"), commands in column A, variables (&lt;&lt;hostname&gt;&gt;) in B, notes in C. Each sheet becomes a template that
          every matching controller's running configuration is checked against. Headings, examples, exec-only steps and procedural
          shutdowns are dropped; lines the notes call conditional ("if needed", regional blocks) become warnings. When the standard
          changes, import the new workbook: sheets of the same name are replaced.
        </p>
        <input ref={standardInput} type="file" accept=".xlsx,.xlsm" disabled={ro || importing !== null} onChange={(e) => void handleImport("standard", e.target.files?.[0])} />
        {importing === "standard" && <span className="table-hint"> Importing…</span>}
        {current.template_profiles.length > 0 && (
          <table className="data-table" style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>Profile</th>
                <th>Platform</th>
                <th>Lines</th>
                <th>Optional</th>
                <th>Skipped</th>
                <th>Imported</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {current.template_profiles.map((p, index) => (
                <Fragment key={p.name}>
                  <tr>
                    <td>
                      <strong>{p.name}</strong>
                    </td>
                    <td>
                      {p.platform === "9800" ? "Catalyst 9800" : "AireOS"} · {p.kind === "flex" ? "FlexConnect / vWLC" : "local mode"}
                    </td>
                    <td>{p.rules}</td>
                    <td>{p.optional}</td>
                    <td>{p.skipped_count}</td>
                    <td className="table-hint">
                      {p.source} {p.imported_at ? new Date(p.imported_at).toLocaleString() : ""}
                    </td>
                    <td>
                      <button className="link-button" onClick={() => setOpenProfile(openProfile === p.name ? null : p.name)}>
                        {openProfile === p.name ? "Hide" : "View / edit"}
                      </button>
                      {isAdmin && (
                        <button
                          className="link-button danger"
                          onClick={() => {
                            if (confirm(`Remove the ${p.name} profile?`)) set("template_profiles", current.template_profiles.filter((_, i) => i !== index));
                          }}
                        >
                          Remove
                        </button>
                      )}
                    </td>
                  </tr>
                  {openProfile === p.name && (
                    <tr>
                      <td colSpan={7}>
                        <textarea
                          rows={18}
                          readOnly={ro}
                          className="audit-mono"
                          style={{ width: "100%" }}
                          value={p.template}
                          onChange={(e) => set("template_profiles", current.template_profiles.map((x, i) => (i === index ? { ...x, template: e.target.value } : x)))}
                        />
                        {p.skipped.length > 0 && (
                          <details>
                            <summary className="table-hint">{p.skipped_count} sheet lines not used as checks (why each was skipped)</summary>
                            <table className="data-table audit-subtable">
                              <tbody>
                                {p.skipped.map((s, i) => (
                                  <tr key={i}>
                                    <td>row {s.row}</td>
                                    <td className="audit-mono">{s.text}</td>
                                    <td className="table-hint">{s.reason}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </details>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
        {current.import_history.length > 0 && (
          <p className="table-hint" style={{ marginTop: 8 }}>
            Last imports: {current.import_history.slice(0, 5).map((h) => `${h.filename} (${new Date(h.imported_at).toLocaleDateString()}, ${h.imported_by ?? "?"})`).join(" · ")}
          </p>
        )}
      </div>

      <div className="card-form">
        <h2 style={{ marginTop: 0 }}>Return to Operations checklist</h2>
        <p className="page-subtitle">
          The R2O workbook: one sheet per controller build, one row per check with its command(s), expected value, pass/fail criteria,
          PRE/POST cutover and Mandatory/Gold. Each row's criteria are read for checkable statements ("Telnet must be "Disable"", "only
          those 2 destination IP", "must match the first 8 characters of device name"); rows that cannot be checked automatically are
          listed for manual review with the collected output. XXXXXXX / &lt;&lt;SITECODE&gt;&gt; in a command becomes the site code (first 8
          characters of the device name).
        </p>
        <input ref={r2oInput} type="file" accept=".xlsx,.xlsm" disabled={ro || importing !== null} onChange={(e) => void handleImport("r2o", e.target.files?.[0])} />
        {importing === "r2o" && <span className="table-hint"> Importing…</span>}
        {current.r2o_checklists.length > 0 && (
          <table className="data-table" style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>Checklist</th>
                <th>Platform</th>
                <th>Checks</th>
                <th>Imported</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {current.r2o_checklists.map((c, ci) => (
                <Fragment key={c.name}>
                  <tr>
                    <td>
                      <strong>{c.name}</strong>
                    </td>
                    <td>
                      {c.platform === "9800" ? "Catalyst 9800" : "AireOS"} · {c.kind === "flex" ? "FlexConnect" : "local mode"}
                    </td>
                    <td>
                      {c.items.filter((i) => i.enabled).length} of {c.items.length} enabled
                    </td>
                    <td className="table-hint">
                      {c.source} {c.imported_at ? new Date(c.imported_at).toLocaleString() : ""}
                    </td>
                    <td>
                      <button className="link-button" onClick={() => setOpenChecklist(openChecklist === c.name ? null : c.name)}>
                        {openChecklist === c.name ? "Hide" : "Checks"}
                      </button>
                      {isAdmin && (
                        <button
                          className="link-button danger"
                          onClick={() => {
                            if (confirm(`Remove the ${c.name} checklist?`)) set("r2o_checklists", current.r2o_checklists.filter((_, i) => i !== ci));
                          }}
                        >
                          Remove
                        </button>
                      )}
                    </td>
                  </tr>
                  {openChecklist === c.name && (
                    <tr>
                      <td colSpan={5}>
                        <table className="data-table audit-subtable">
                          <thead>
                            <tr>
                              <th>On</th>
                              <th>Row</th>
                              <th>Check</th>
                              <th>Command(s)</th>
                              <th>Stage</th>
                              <th>Level</th>
                            </tr>
                          </thead>
                          <tbody>
                            {c.items.map((item, ii) => (
                              <tr key={item.item_id}>
                                <td>
                                  <input
                                    type="checkbox"
                                    disabled={ro}
                                    checked={item.enabled}
                                    onChange={(e) =>
                                      set(
                                        "r2o_checklists",
                                        current.r2o_checklists.map((x, xi) =>
                                          xi === ci ? { ...x, items: x.items.map((it, iti) => (iti === ii ? { ...it, enabled: e.target.checked } : it)) } : x,
                                        ),
                                      )
                                    }
                                  />
                                </td>
                                <td>{item.row}</td>
                                <td>
                                  {item.description}
                                  {item.criteria ? <div className="table-hint">{item.criteria.slice(0, 200)}</div> : null}
                                </td>
                                <td className="audit-mono">{item.commands.join("\n")}</td>
                                <td>{item.stage ?? "—"}</td>
                                <td>{item.level ?? "—"}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {(profileNames.length > 0 || checklistNames.length > 0) && controllers.length > 0 && (
        <div className="card-form">
          <h2 style={{ marginTop: 0 }}>Which profile applies to which controller</h2>
          <p className="page-subtitle">
            Automatic: the only profile for the controller's platform, else the FlexConnect one for a C9800-CL or a 9800 whose site tags are all
            "no local-site", else the local-mode one. Override per controller here.
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>Controller</th>
                <th>Configuration standard</th>
                <th>R2O checklist</th>
              </tr>
            </thead>
            <tbody>
              {controllers.map((d) => (
                <tr key={d.id}>
                  <td>
                    {d.name} <span className="table-hint">{d.device_type === "cisco_wlc" ? "AireOS" : "9800"}</span>
                  </td>
                  <td>
                    <select
                      disabled={ro}
                      value={current.profile_assignments[d.name] ?? ""}
                      onChange={(e) => {
                        const next = { ...current.profile_assignments };
                        if (e.target.value) next[d.name] = e.target.value;
                        else delete next[d.name];
                        set("profile_assignments", next);
                      }}
                    >
                      <option value="">Automatic</option>
                      <option value="none">None (skip)</option>
                      {profileNames.map((n) => (
                        <option key={n}>{n}</option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <select
                      disabled={ro}
                      value={current.r2o_assignments[d.name] ?? ""}
                      onChange={(e) => {
                        const next = { ...current.r2o_assignments };
                        if (e.target.value) next[d.name] = e.target.value;
                        else delete next[d.name];
                        set("r2o_assignments", next);
                      }}
                    >
                      <option value="">Automatic</option>
                      <option value="none">None (skip)</option>
                      {checklistNames.map((n) => (
                        <option key={n}>{n}</option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card-form">
        <h2 style={{ marginTop: 0 }}>Audit settings</h2>
        <div className="form-grid">
          <label>
            AP naming pattern (regular expression)
            <input disabled={ro} value={current.ap_name_pattern} onChange={(e) => set("ap_name_pattern" as TextKey, e.target.value)} placeholder="^[A-Z0-9]{8}-AP\d{3}$" />
          </label>
          <label>
            AP name case
            <select disabled={ro} value={current.ap_name_case} onChange={(e) => set("ap_name_case", e.target.value as WirelessStandard["ap_name_case"])}>
              <option value="any">Any</option>
              <option value="upper">Upper case</option>
              <option value="lower">Lower case</option>
            </select>
          </label>
          <label>
            DNS domain for AP names
            <input disabled={ro} value={current.dns_domain} onChange={(e) => set("dns_domain" as TextKey, e.target.value)} placeholder="corp.example.com" />
          </label>
          <label>
            AP group / site tag naming pattern
            <input
              disabled={ro}
              value={current.ap_group_name_pattern}
              onChange={(e) => set("ap_group_name_pattern" as TextKey, e.target.value)}
              placeholder="^[A-Z0-9]{8}-APGROUP\d{2}(-(Production|Warehouse))?(-Outside)?$"
            />
          </label>
          {LISTS.map(({ key, label, hint }) => (
            <label key={key}>
              {label}
              <textarea disabled={ro} rows={3} value={lists[key] ?? ""} onChange={(e) => setLists({ ...lists, [key]: e.target.value })} placeholder={hint} />
            </label>
          ))}
          <label>
            2.4 GHz channel plan
            <textarea disabled={ro} rows={2} value={lists.allowed_24ghz_channels ?? ""} onChange={(e) => setLists({ ...lists, allowed_24ghz_channels: e.target.value })} />
          </label>
          <label>
            Template variables (name=value)
            <textarea
              disabled={ro}
              rows={3}
              value={lists.template_variables ?? ""}
              onChange={(e) => setLists({ ...lists, template_variables: e.target.value })}
              placeholder={"regional_dns_server=192.0.2.53"}
            />
          </label>
          {NUMBERS.map(({ key, label }) => (
            <label key={key}>
              {label}
              <input disabled={ro} type="number" step="any" value={current[key]} onChange={(e) => set(key, Number(e.target.value))} />
            </label>
          ))}
        </div>
        <div className="audit-checks">
          {BOOLS.map(({ key, label }) => (
            <label key={key} className="inline-check">
              <input disabled={ro} type="checkbox" checked={current[key]} onChange={(e) => set(key, e.target.checked)} /> {label}
            </label>
          ))}
        </div>
        <details style={{ marginTop: 12 }}>
          <summary>Hand-written templates (used when no profile is imported for a platform)</summary>
          <div className="form-grid" style={{ marginTop: 8 }}>
            <label>
              Catalyst 9800
              <textarea disabled={ro} rows={8} className="audit-mono" value={current.template_9800} onChange={(e) => set("template_9800", e.target.value)} />
            </label>
            <label>
              AireOS
              <textarea disabled={ro} rows={8} className="audit-mono" value={current.template_aireos} onChange={(e) => set("template_aireos", e.target.value)} />
            </label>
          </div>
          <p className="table-hint">
            One line per requirement; indentation nests under the line above. Prefixes: <code>absent:</code> must not be configured,{" "}
            <code>optional:</code> a warning only, <code>re:</code> a regular expression. <code>{"{{name}}"}</code> is any value (or the template
            variable of that name), <code>{"{{*}}"}</code> anything.
          </p>
        </details>
        {isAdmin && (
          <div className="page-actions" style={{ marginTop: 14 }}>
            <button onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save standard"}
            </button>
            {data.updated_at && <span className="table-hint">Last saved {new Date(data.updated_at).toLocaleString()}</span>}
          </div>
        )}
      </div>
    </div>
  );
}
