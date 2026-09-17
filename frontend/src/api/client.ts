import axios from "axios";

// Where the API lives. VITE_API_BASE_URL wins when set (docker-compose sets
// it; an empty string means "same origin"). Without it the dev server talks
// to the backend's default port, and a production build assumes the API is
// serving the pages itself (the desktop bundle does exactly that), so
// requests are relative and work on whatever host and port the app has.
function defaultApiBase(): string {
  const configured = import.meta.env.VITE_API_BASE_URL;
  if (configured !== undefined) return configured;
  return import.meta.env.DEV ? "http://localhost:8000" : "";
}

export const API_BASE_URL = defaultApiBase();

/** The API base as an absolute URL, for places that cannot use a relative
 *  one (opening a WebSocket). */
export function absoluteApiBase(): string {
  if (API_BASE_URL) return API_BASE_URL;
  return window.location.origin;
}

export const apiClient = axios.create({ baseURL: API_BASE_URL });

const TOKEN_KEY = "configcollector_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

apiClient.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      clearToken();
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  },
);

export function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((d) => d.msg ?? JSON.stringify(d)).join(", ");
    }
  }
  return "Something went wrong. Please try again.";
}
