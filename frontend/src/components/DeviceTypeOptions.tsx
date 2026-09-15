import type { DeviceType } from "../api/types";

/** The <option>s for a device-type <select>: built-in types first, then the
 *  org's own custom types under their own heading (only when there are any),
 *  so both the Add-device form and the bulk-add rows list them identically. */
export function DeviceTypeOptions({ deviceTypes }: { deviceTypes: DeviceType[] }) {
  const builtIn = deviceTypes.filter((t) => !t.custom);
  const custom = deviceTypes.filter((t) => t.custom);
  if (custom.length === 0) {
    return (
      <>
        {builtIn.map((t) => (
          <option key={t.key} value={t.key}>
            {t.label}
          </option>
        ))}
      </>
    );
  }
  return (
    <>
      <optgroup label="Built-in">
        {builtIn.map((t) => (
          <option key={t.key} value={t.key}>
            {t.label}
          </option>
        ))}
      </optgroup>
      <optgroup label="Your device types">
        {custom.map((t) => (
          <option key={t.key} value={t.key}>
            {t.label}
          </option>
        ))}
      </optgroup>
    </>
  );
}
