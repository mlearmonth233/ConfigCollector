import { getToken } from "./api/client";
import { logsApi } from "./api/resources";

/** Sends uncaught browser-side errors to the backend so they land in
 * packrat-api.log next to the server events of the same moment. Only
 * while logged in (the endpoint needs a token), rate-limited so a render
 * loop can't flood the log, and never throws itself. */

const MAX_REPORTS_PER_MINUTE = 10;
let windowStart = 0;
let sentInWindow = 0;
const recent = new Set<string>();

export function reportClientError(kind: string, message: string, stack?: string): void {
  try {
    if (!getToken()) return;
    const now = Date.now();
    if (now - windowStart > 60_000) {
      windowStart = now;
      sentInWindow = 0;
      recent.clear();
    }
    const key = `${kind}:${message}`;
    if (sentInWindow >= MAX_REPORTS_PER_MINUTE || recent.has(key)) return;
    sentInWindow += 1;
    recent.add(key);
    void logsApi
      .reportClientError({
        kind,
        message: message.slice(0, 2000),
        source: window.location.href.slice(0, 500),
        stack: stack?.slice(0, 8000),
      })
      .catch(() => undefined);
  } catch {
    // Reporting must never become its own error.
  }
}

function describe(reason: unknown): { message: string; stack?: string } {
  if (reason instanceof Error) return { message: `${reason.name}: ${reason.message}`, stack: reason.stack };
  if (typeof reason === "string") return { message: reason };
  try {
    return { message: JSON.stringify(reason).slice(0, 500) };
  } catch {
    return { message: String(reason) };
  }
}

export function installErrorReporting(): void {
  window.addEventListener("error", (event) => {
    const { message, stack } = describe(event.error ?? event.message);
    const where = event.filename ? ` (${event.filename}:${event.lineno}:${event.colno})` : "";
    reportClientError("error", message + where, stack);
  });
  window.addEventListener("unhandledrejection", (event) => {
    // Failed API calls already show up in the server log as the request
    // itself; only rejections that aren't axios errors are news.
    const reason = event.reason as { isAxiosError?: boolean } | undefined;
    if (reason && typeof reason === "object" && reason.isAxiosError) return;
    const { message, stack } = describe(event.reason);
    reportClientError("unhandledrejection", message, stack);
  });
}
