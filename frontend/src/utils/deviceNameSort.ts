// Site naming convention's role code, embedded as a substring in the
// hostname (e.g. "GBGYSP01SWA001"). Order here is priority for display -
// not app.services.hostname_detection's own match order (which doesn't
// matter there since none of its patterns overlap).
const ROLE_SORT_ORDER = ["FWL", "RTR", "SWC", "SWD", "WLC", "SWS", "SWA", "CON", "PDU"];

function roleSortRank(name: string): number {
  const upper = name.toUpperCase();
  const rank = ROLE_SORT_ORDER.findIndex((code) => upper.includes(code));
  return rank === -1 ? ROLE_SORT_ORDER.length : rank;
}

function trailingNumber(name: string): number {
  const match = /(\d+)$/.exec(name);
  return match ? Number(match[1]) : Number.POSITIVE_INFINITY;
}

/** Orders two device names by role code in ROLE_SORT_ORDER (a name with no
 * recognized code sorts after all of them), then numerically ascending by
 * each name's trailing number, then alphabetically as a last resort - e.g.
 * RTR01, RTR02, then SWC01, SWC02, ... */
export function compareDeviceNames(a: string, b: string): number {
  return roleSortRank(a) - roleSortRank(b) || trailingNumber(a) - trailingNumber(b) || a.localeCompare(b);
}

/** Sorts any list of items by a name extracted from each one, using
 * compareDeviceNames - shared so the "bulk add from hostnames" review table
 * and the main Devices list always agree on the same order regardless of
 * how (or in what order) devices were actually added. */
export function sortByDeviceName<T>(items: T[], getName: (item: T) => string): T[] {
  return [...items].sort((a, b) => compareDeviceNames(getName(a), getName(b)));
}
