import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const EMPTY = { stats: null, blogPosts: [], agents: [], activity: [] };

// API_URL and INTERNAL_API_URL are read from the environment, so load the
// module fresh after each env change.
async function loadFetchHomeData() {
  vi.resetModules();
  return (await import("./homeData")).fetchHomeData;
}

beforeEach(() => {
  vi.stubEnv("INTERNAL_API_URL", "");
  vi.stubEnv("NEXT_PUBLIC_API_URL", "");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("fetchHomeData", () => {
  it("does not fetch when no absolute server-side base URL exists (build time)", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const fetchHomeData = await loadFetchHomeData();

    expect(await fetchHomeData()).toEqual(EMPTY);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fetches from INTERNAL_API_URL when set", async () => {
    vi.stubEnv("INTERNAL_API_URL", "http://backend:8000");
    const fetchMock = vi.fn<(url: string) => Promise<Response>>(async () => ({ ok: false }) as Response);
    vi.stubGlobal("fetch", fetchMock);

    const fetchHomeData = await loadFetchHomeData();
    await fetchHomeData();

    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://backend:8000/api/v1/agents/stats");
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ next: { revalidate: 300 } });
  });

  it("gives every request a bounded timeout signal and survives the abort", async () => {
    vi.stubEnv("INTERNAL_API_URL", "http://backend:8000");
    // The timer behind AbortSignal.timeout is native, not faked: capture the
    // requested delay and hand back an already-aborted signal instead.
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout").mockImplementation(() => AbortSignal.abort());
    const fetchMock = vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          if (init?.signal?.aborted) reject(init.signal.reason);
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const fetchHomeData = await loadFetchHomeData();

    expect(await fetchHomeData()).toEqual(EMPTY);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(timeoutSpy).toHaveBeenCalledTimes(4);
    for (const ms of timeoutSpy.mock.calls.map((c) => c[0])) expect(ms).toBeLessThanOrEqual(10_000);
    timeoutSpy.mockRestore();
  });
});
