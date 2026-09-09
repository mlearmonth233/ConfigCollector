import { useMemo, useState } from "react";

import { extractErrorMessage } from "../api/client";
import { commandProfilesApi } from "../api/resources";
import type { CommandProfile } from "../api/types";

function parseExtra(raw: string): string[] {
  return raw
    .split(",")
    .map((c) => c.trim())
    .filter((c) => c.length > 0);
}

interface Props {
  profile: CommandProfile;
  onSaved: (updated: CommandProfile) => void;
}

/** One device type's command configuration: a checklist of common "show"
 * commands for its category, plus free text for anything else. Cisco IOS
 * defaults to "show tech-support" out of the box; every device type can be
 * customized the same way and reset back to that built-in default. */
export function CommandProfileCard({ profile, onSaved }: Props) {
  const initialChecked = useMemo(
    () => new Set(profile.commands.filter((c) => profile.suggested_commands.includes(c))),
    [profile]
  );
  const initialExtra = useMemo(
    () => profile.commands.filter((c) => !profile.suggested_commands.includes(c)).join(", "),
    [profile]
  );

  const [checked, setChecked] = useState<Set<string>>(initialChecked);
  const [extra, setExtra] = useState(initialExtra);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const effectiveCommands = useMemo(() => {
    const ordered = [
      ...profile.suggested_commands.filter((c) => checked.has(c)),
      ...parseExtra(extra),
    ];
    return Array.from(new Set(ordered));
  }, [profile.suggested_commands, checked, extra]);

  const isDirty =
    checked.size !== initialChecked.size ||
    [...checked].some((c) => !initialChecked.has(c)) ||
    extra !== initialExtra;

  function toggle(command: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(command)) next.delete(command);
      else next.add(command);
      return next;
    });
  }

  async function handleSave() {
    if (effectiveCommands.length === 0) {
      setError("Select at least one command, or add one, before saving.");
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const { data } = await commandProfilesApi.save(profile.device_type, effectiveCommands);
      onSaved(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleReset() {
    setError(null);
    setSaving(true);
    try {
      const { data } = await commandProfilesApi.reset(profile.device_type);
      onSaved(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card-form command-profile-card">
      <div className="page-header-row" style={{ marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>
          {profile.label}
          {profile.is_custom && <span className="status-badge status-fallback">customized</span>}
        </h3>
        {profile.is_custom && (
          <button className="link-button" onClick={handleReset} disabled={saving}>
            Reset to default
          </button>
        )}
      </div>

      {error && <div className="error-banner">{error}</div>}

      {profile.suggested_commands.length > 0 ? (
        <div className="command-checklist">
          {profile.suggested_commands.map((command) => (
            <label key={command} className="checkbox-label">
              <input type="checkbox" checked={checked.has(command)} onChange={() => toggle(command)} />
              <code>{command}</code>
            </label>
          ))}
        </div>
      ) : (
        <p className="field-hint">No suggested commands for this category yet - add your own below.</p>
      )}

      <label style={{ marginTop: 12 }}>
        Extra commands (comma-separated)
        <input value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="e.g. show clock" />
      </label>

      <div className="page-header-row" style={{ marginTop: 12 }}>
        <p className="page-subtitle" style={{ margin: 0 }}>
          {effectiveCommands.length > 0
            ? `Runs ${effectiveCommands.length} command${effectiveCommands.length === 1 ? "" : "s"}: ${effectiveCommands.join(", ")}`
            : "No commands selected yet."}
        </p>
        <button onClick={handleSave} disabled={saving || !isDirty}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
