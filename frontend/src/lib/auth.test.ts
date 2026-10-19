// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { clearAuthSession, fetchWithAuth, getAuthSnapshot, getServerAuthSnapshot, refreshAccessToken, subscribeAuthChanges } from "./auth";

function pendingResponse() {
  let finish: (response: Response) => void = () => {};
  const promise = new Promise<Response>(resolve => { finish = resolve; });
  return { promise, finish };
}
function session(owner: string) {
  localStorage.setItem("access_token", `${owner}-access`);
  localStorage.setItem("refresh_token", `${owner}-refresh`);
}
function unchanged(owner: string) {
  return localStorage.getItem("access_token") === `${owner}-access` && localStorage.getItem("refresh_token") === `${owner}-refresh`;
}
function renewed(owner: string) {
  return new Response(JSON.stringify({ access_token: `${owner}-new`, refresh_token: `${owner}-new-refresh` }));
}
beforeEach(() => { localStorage.clear(); vi.stubGlobal("fetch", vi.fn()); });
afterEach(() => { vi.unstubAllGlobals(); });

it.each([true, false])("preserves replacement on late refresh (success=%s)", async success => {
  session("first");
  const response = pendingResponse();
  vi.mocked(fetch).mockReturnValue(response.promise);
  const refresh = refreshAccessToken();
  session("second");
  response.finish(success ? renewed("first") : new Response(null, { status: 401 }));
  expect((await refresh) === null).toBe(true);
  expect(unchanged("second")).toBe(true);
});

it("does not restore a logged-out session", async () => {
  session("first");
  const response = pendingResponse();
  vi.mocked(fetch).mockReturnValue(response.promise);
  const refresh = refreshAccessToken();
  localStorage.clear();
  response.finish(renewed("first"));
  expect((await refresh) === null).toBe(true);
  expect(localStorage.getItem("access_token") === null).toBe(true);
});

it("deduplicates only the current session and keeps newer pending refresh", async () => {
  session("first");
  const first = pendingResponse(), second = pendingResponse();
  vi.mocked(fetch).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  const old = refreshAccessToken();
  session("second");
  const current = refreshAccessToken();
  first.finish(renewed("first"));
  await old;
  const duplicate = refreshAccessToken();
  second.finish(renewed("second"));
  const values = await Promise.all([current, duplicate]);
  expect(values.every(value => value === "second-new")).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("does not retry a delayed 401 after changing owners", async () => {
  session("first");
  const response = pendingResponse();
  vi.mocked(fetch).mockReturnValueOnce(response.promise).mockResolvedValueOnce(renewed("second")).mockResolvedValue(new Response(null));
  const operation = fetchWithAuth("/mutation", { method: "POST" });
  session("second");
  response.finish(new Response(null, { status: 401 }));
  expect((await operation).status).toBe(401);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(unchanged("second")).toBe(true);
});

it("keeps a successful mutation acknowledgement across normal refresh", async () => {
  session("first");
  const response = pendingResponse();
  vi.mocked(fetch).mockReturnValueOnce(response.promise).mockResolvedValueOnce(renewed("first"));
  const operation = fetchWithAuth("/mutation", { method: "POST" });
  await refreshAccessToken();
  response.finish(new Response(null, { status: 201 }));
  expect((await operation).status).toBe(201);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("retries a 401 normally in the original session", async () => {
  session("first");
  vi.mocked(fetch).mockResolvedValueOnce(new Response(null, { status: 401 })).mockResolvedValueOnce(renewed("first")).mockResolvedValueOnce(new Response(null, { status: 204 }));
  expect((await fetchWithAuth("/mutation", { method: "POST" })).status).toBe(204);
  expect(fetch).toHaveBeenCalledTimes(3);
});

it("blocks the retry when the owner changes during refresh", async () => {
  session("first");
  const response = pendingResponse();
  vi.mocked(fetch).mockResolvedValueOnce(new Response(null, { status: 401 })).mockReturnValueOnce(response.promise);
  const operation = fetchWithAuth("/mutation", { method: "POST" });
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  session("second");
  response.finish(renewed("first"));
  expect((await operation).status).toBe(401);
  expect(unchanged("second")).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("retains identity across rotation and recovers a synchronously failed refresh", async () => {
  session("first");
  const captured = getAuthSnapshot();
  vi.mocked(fetch).mockImplementationOnce(() => { throw new Error("Synthetic transport failure"); }).mockResolvedValueOnce(renewed("first"));
  expect((await refreshAccessToken()) === null).toBe(true);
  expect((await refreshAccessToken()) !== null).toBe(true);
  expect(getAuthSnapshot().generation === captured.generation).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("observes direct writes, notifies logout, and cleans up subscriptions", () => {
  vi.useFakeTimers();
  session("first");
  const captured = getAuthSnapshot();
  const observed = vi.fn(() => getAuthSnapshot());
  const stop = subscribeAuthChanges(observed);
  session("second");
  vi.advanceTimersByTime(1000);
  expect(getAuthSnapshot().generation !== captured.generation).toBe(true);
  clearAuthSession(captured.generation);
  expect(unchanged("second")).toBe(true);
  clearAuthSession();
  expect(localStorage.getItem("access_token") === null).toBe(true);
  expect(observed).toHaveBeenCalledTimes(2);
  stop();
  window.dispatchEvent(new Event("storage"));
  window.dispatchEvent(new Event("focus"));
  vi.advanceTimersByTime(1000);
  expect(observed).toHaveBeenCalledTimes(2);
  expect(vi.getTimerCount()).toBe(0);
  vi.useRealTimers();
});

it("provides the same SSR snapshot without reading browser storage", () => {
  const read = vi.spyOn(Storage.prototype, "getItem");
  expect(getServerAuthSnapshot() === getServerAuthSnapshot()).toBe(true);
  expect(read).not.toHaveBeenCalled();
  read.mockRestore();
});

it.each(["access_token", "refresh_token"])("fails closed with a stable anonymous snapshot when %s cannot be read", async deniedKey => {
  session("synthetic");
  getAuthSnapshot();
  const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(key => {
    if (key === deniedKey) throw new DOMException("Synthetic storage denied", "SecurityError");
    return "synthetic-session-value";
  });
  try {
    const anonymous = getAuthSnapshot();
    expect(anonymous.accessToken).toBeNull();
    expect(anonymous.refreshToken).toBeNull();
    expect(getAuthSnapshot()).toBe(anonymous);
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }));
    await fetchWithAuth("/synthetic-public-read");
    const options = vi.mocked(fetch).mock.calls[0]?.[1];
    expect(new Headers(options?.headers).has("Authorization")).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
  } finally { read.mockRestore(); }
});

it("advances generation on denied storage and restores a readable valid session", () => {
  session("synthetic");
  const original = getAuthSnapshot();
  const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new DOMException("Synthetic storage denied", "SecurityError");
  });
  let deniedGeneration: number;
  try {
    const denied = getAuthSnapshot();
    deniedGeneration = denied.generation;
    expect(denied.generation).toBeGreaterThan(original.generation);
    expect(getAuthSnapshot()).toBe(denied);
    clearAuthSession(original.generation);
  } finally { read.mockRestore(); }
  const restored = getAuthSnapshot();
  expect(restored.generation).toBeGreaterThan(deniedGeneration);
  expect(restored.accessToken).toBe(original.accessToken);
  expect(restored.refreshToken).toBe(original.refreshToken);
  expect(getAuthSnapshot()).toBe(restored);
});

it("sign-out also removes the project-page auth entry", () => {
  session("first");
  localStorage.setItem("auth", JSON.stringify({ token: "t", email: "a@b.c", userId: "1" }));
  clearAuthSession();
  expect(localStorage.getItem("auth")).toBeNull();
  expect(localStorage.getItem("access_token")).toBeNull();
});

it("a stale sign-out preserves the replacement session and its project-page auth entry", () => {
  session("first");
  const captured = getAuthSnapshot();
  session("second");
  const legacy = JSON.stringify({ token: "synthetic-second", email: "synthetic@example.test", userId: "synthetic-user" });
  localStorage.setItem("auth", legacy);
  clearAuthSession(captured.generation);
  expect(unchanged("second")).toBe(true);
  expect(localStorage.getItem("auth")).toBe(legacy);
});
