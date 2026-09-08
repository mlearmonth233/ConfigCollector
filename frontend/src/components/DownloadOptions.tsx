import type { DownloadPrefs, FileExtension } from "../hooks/useDownloadPrefs";

interface Props {
  prefs: DownloadPrefs;
  onChange: (next: Partial<DownloadPrefs>) => void;
}

/** File type + "add timestamp to filename" controls, shared between the
 * single-snapshot download modal and the job-wide bulk download. */
export function DownloadOptions({ prefs, onChange }: Props) {
  return (
    <div className="download-options">
      <label className="inline-label">
        File type
        <select value={prefs.extension} onChange={(e) => onChange({ extension: e.target.value as FileExtension })}>
          <option value="txt">.txt</option>
          <option value="log">.log</option>
        </select>
      </label>
      <label className="inline-label checkbox-label">
        <input
          type="checkbox"
          checked={prefs.includeTimestamp}
          onChange={(e) => onChange({ includeTimestamp: e.target.checked })}
        />
        Add timestamp to filename
      </label>
    </div>
  );
}
