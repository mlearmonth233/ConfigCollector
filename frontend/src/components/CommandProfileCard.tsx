import { useMemo, useState } from "react";

import { extractErrorMessage } from "../api/client";
import { commandProfilesApi } from "../api/resources";
import type { CommandProfile } from "../api/types";

function parseCommands(raw: string): string[] {
  return raw
    .split(",")
    .map((c) => c.trim())
    .filter((c) => c.length > 0);
}

interface Props {
  profile: CommandProfile;
  onSaved: (updated: CommandProfile) => void;
}

/** One device type's command configuration: a single comma-separated list,
 * editable and resettable back to its built-in default. */
export function CommandProfileCard({ profile, onSaved }: Props) {
  const initialText = useMemo(() => profile.commands.join(", "), [profile]);

  const [text, setText] = useState(initialText);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const effectiveCommands = useMemo(() => parseCommands(text), [text]);
  const isDirty = text !== initialText;

  async function handleSave() {
    if (effectiveCommands.length === 0) {
      setError("Enter at least one command before saving.");
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

      <label style={{ marginTop: 12 }}>
        Commands (comma-separated)
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. show clock" />
      </label>

      <div className="page-header-row" style={{ marginTop: 12 }}>
        <p className="page-subtitle" style={{ margin: 0 }}>
          {effectiveCommands.length > 0
            ? `Runs ${effectiveCommands.length} command${effectiveCommands.length === 1 ? "" : "s"}: ${effectiveCommands.join(", ")}`
            : "No commands entered yet."}
        </p>
        <button onClick={handleSave} disabled={saving || !isDirty}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
