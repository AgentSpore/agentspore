import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const EMPTY = { stats: null, blogPosts: [], agents: [], activity: [], services: { status: "loading", projects: [] } };

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
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => ({ ok: false }) as Response);
    vi.stubGlobal("fetch", fetchMock);

    const fetchHomeData = await loadFetchHomeData();
    await fetchHomeData();

    expect(fetchMock).toHaveBeenCalledTimes(5);
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

    expect(await fetchHomeData()).toEqual({ ...EMPTY, services: { status: "error", projects: [] } });
    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(timeoutSpy).toHaveBeenCalledTimes(5);
    for (const ms of timeoutSpy.mock.calls.map((c) => c[0])) expect(ms).toBeLessThanOrEqual(10_000);
    timeoutSpy.mockRestore();
  });
});


it("loads the real catalog independently when all other sources fail", async () => {
  vi.stubEnv("INTERNAL_API_URL", "https://api.example.test");
  const project = { id: "00000000-0000-4000-8000-000000000001", title: "A real API project", status: "deployed", deploy_url: "https://service.example.test" };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/projects?limit=200")) return new Response(JSON.stringify([project]));
    throw new Error("Other source unavailable");
  }));
  const result = await (await loadFetchHomeData())();
  expect(result.stats).toBeNull();
  expect(result.services?.status).toBe("ready");
  expect(result.services?.projects[0]?.title).toBe(project.title);
});

it.each([{}, [null], [{ title: "broken" }]])("keeps malformed catalog distinct from a verified empty response: %j", async payload => {
  vi.stubEnv("INTERNAL_API_URL", "https://api.example.test");
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(payload))));
  expect((await (await loadFetchHomeData())()).services).toEqual({ status: "error", projects: [] });
});

it("retains available platform statistics when catalog HTTP fails", async () => {
  vi.stubEnv("INTERNAL_API_URL", "https://api.example.test");
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("/agents/stats") ? new Response(JSON.stringify({ total_projects: 20 })) : new Response("Unavailable", { status: 503 })));
  const result = await (await loadFetchHomeData())();
  expect(result.stats?.total_projects).toBe(20);
  expect(result.services).toEqual({ status: "error", projects: [] });
});

it("represents a valid empty catalog as ready", async () => {
  vi.stubEnv("INTERNAL_API_URL", "https://api.example.test");
  vi.stubGlobal("fetch", vi.fn(async () => new Response("[]")));
  expect((await (await loadFetchHomeData())()).services).toEqual({ status: "ready", projects: [] });
});
