import { useEffect, useMemo, useState } from "react";

import { extractErrorMessage } from "../api/client";
import { commandProfilesApi } from "../api/resources";
import type { CommandProfile } from "../api/types";
import { CommandProfileCard } from "../components/CommandProfileCard";
import { CustomDeviceTypesSection } from "../components/CustomDeviceTypesSection";

export function Commands() {
  const [profiles, setProfiles] = useState<CommandProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    try {
      const { data } = await commandProfilesApi.list();
      setProfiles(data);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  const grouped = useMemo(() => {
    const byCategory = new Map<string, CommandProfile[]>();
    for (const p of [...profiles].sort((a, b) => a.label.localeCompare(b.label))) {
      const list = byCategory.get(p.category) ?? [];
      list.push(p);
      byCategory.set(p.category, list);
    }
    return Array.from(byCategory.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [profiles]);

  function handleSaved(updated: CommandProfile) {
    setProfiles((prev) => prev.map((p) => (p.device_type === updated.device_type ? updated : p)));
  }

  return (
    <div className="page">
      <h1>Commands</h1>
      <p className="page-subtitle">
        Choose which command(s) run for each device type by default - a Cisco IOS switch or router
        collects <code>show tech-support</code> unless you change it here. Pick from common show
        commands or add your own; a device's own custom command (set on the device itself) still wins
        over whatever's saved here, and a one-time override entered when starting a collection wins over
        both.
      </p>
      <p className="page-subtitle">
        A command containing <code>{"{client}"}</code> runs once per wireless client: at collection time it is repeated for every
        MAC address listed by an earlier client summary in the same list ("show wireless client summary" on a 9800, "show client
        summary" on AireOS). That is how the Inventory learns each client's key management and whether it is on 802.11r Fast
        Transition. Every client is one more command on the controller, so a busy site's collection takes a few minutes longer.
      </p>
      {error && <div className="error-banner">{error}</div>}

      <CustomDeviceTypesSection />

      <h2 className="category-heading" style={{ textTransform: "none", marginTop: 8 }}>
        Built-in device types
      </h2>
      {loading ? (
        <p>Loading…</p>
      ) : (
        grouped.map(([category, items]) => (
          <section key={category} style={{ marginBottom: 24 }}>
            <h2 className="category-heading">{category}</h2>
            {items.map((profile) => (
              <CommandProfileCard
                key={`${profile.device_type}:${profile.is_custom}:${profile.commands.join("|")}`}
                profile={profile}
                onSaved={handleSaved}
              />
            ))}
          </section>
        ))
      )}
    </div>
  );
}
