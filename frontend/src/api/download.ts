import type { AxiosResponse } from "axios";

/** Saves an axios blob response as a file, using the filename from its
 * Content-Disposition header when present. Both the download endpoints
 * (single snapshot, job-wide zip) require a Bearer token, which a plain
 * <a href> navigation can't send - so the caller fetches via the
 * authenticated axios client with responseType: "blob" first, then this
 * turns that response into an actual saved file. */
export function saveBlobResponse(response: AxiosResponse<Blob>, fallbackFilename: string): void {
  const disposition: string = response.headers["content-disposition"] ?? "";
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const filename = match?.[1] ?? fallbackFilename;

  const url = window.URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}
