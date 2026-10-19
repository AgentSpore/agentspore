import { API_URL } from "./api";

type AuthSnapshot = { generation: number; accessToken: string | null; refreshToken: string | null };
const SERVER_SESSION: AuthSnapshot = { generation: -1, accessToken: null, refreshToken: null };
let session: AuthSnapshot = SERVER_SESSION;
let refreshPending: { generation: number; promise: Promise<string | null> } | null = null;
const listeners = new Set<() => void>();

/** Stable browser snapshot; direct storage replacements advance session identity. */
export function getAuthSnapshot(): AuthSnapshot {
  const accessToken = localStorage.getItem("access_token");
  const refreshToken = localStorage.getItem("refresh_token");
  if (session.generation < 0 || accessToken !== session.accessToken || refreshToken !== session.refreshToken) {
    session = { generation: session.generation + 1, accessToken, refreshToken };
  }
  return session;
}

/** Stable pending snapshot without touching browser storage during SSR. */
export function getServerAuthSnapshot(): AuthSnapshot { return SERVER_SESSION; }

/** Observe cross-tab changes and existing direct same-tab writes; unsubscribe cleans up. */
export function subscribeAuthChanges(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  window.addEventListener("focus", listener);
  const timer = setInterval(listener, 1000);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
    window.removeEventListener("focus", listener);
    clearInterval(timer);
  };
}

/** Clear only the captured session, then synchronously notify mounted consumers. */
export function clearAuthSession(generation = getAuthSnapshot().generation): void {
  if (getAuthSnapshot().generation !== generation) return;
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  localStorage.removeItem("auth");
  getAuthSnapshot();
  listeners.forEach(listener => listener());
}

async function refreshSession(captured: AuthSnapshot): Promise<string | null> {
  try {
    const res = await fetch(`${API_URL}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: captured.refreshToken }),
    });
    if (!res.ok) { clearAuthSession(captured.generation); return null; }
    const data: unknown = await res.json();
    if (getAuthSnapshot().generation !== captured.generation) return null;
    if (typeof data !== "object" || data === null || !("access_token" in data) || !("refresh_token" in data)) return null;
    if (typeof data.access_token !== "string" || typeof data.refresh_token !== "string") return null;
    localStorage.setItem("access_token", data.access_token);
    localStorage.setItem("refresh_token", data.refresh_token);
    session = { generation: captured.generation, accessToken: data.access_token, refreshToken: data.refresh_token };
    listeners.forEach(listener => listener());
    return data.access_token;
  } catch {
    return null;
  }
}

/**
 * Try to refresh the access token using the stored refresh token.
 * Deduplicates concurrent refresh attempts.
 * Returns the new access token or null if refresh failed.
 */
export async function refreshAccessToken(): Promise<string | null> {
  const captured = getAuthSnapshot();
  if (!captured.refreshToken) return null;
  if (refreshPending?.generation === captured.generation) return refreshPending.promise;
  const promise = refreshSession(captured).finally(() => {
    if (refreshPending?.promise === promise) refreshPending = null;
  });
  refreshPending = { generation: captured.generation, promise };
  return promise;
}

/**
 * Fetch with automatic token refresh on 401.
 * Usage: const res = await fetchWithAuth("/api/v1/some/endpoint");
 */
export async function fetchWithAuth(
  url: string,
  options: RequestInit = {},
): Promise<Response> {
  const captured = getAuthSnapshot();
  const headers = new Headers(options.headers);
  if (captured.accessToken) headers.set("Authorization", `Bearer ${captured.accessToken}`);
  let res = await fetch(url, { ...options, headers });
  if (res.status === 401 && getAuthSnapshot().generation === captured.generation) {
    const newToken = await refreshAccessToken();
    if (newToken && getAuthSnapshot().generation === captured.generation) {
      headers.set("Authorization", `Bearer ${newToken}`);
      res = await fetch(url, { ...options, headers });
    }
  }
  return res;
}
