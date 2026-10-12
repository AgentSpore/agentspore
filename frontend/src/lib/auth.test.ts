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
