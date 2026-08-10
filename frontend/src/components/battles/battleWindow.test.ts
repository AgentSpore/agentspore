import { afterEach, describe, expect, it, vi } from "vitest";
import { MAX_LIMIT, PAGE_SIZE, fetchWindow } from "./battleWindow";

type Call = { limit: number; offset: number; status: string | null };

function stubApi(total: number): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", async (url: string) => {
    const params = new URL(url, "http://x").searchParams;
    const limit = Number(params.get("limit"));
    const offset = Number(params.get("offset"));
    calls.push({ limit, offset, status: params.get("status") });
    const rows = Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, i) => ({
      id: `b${offset + i}`,
    }));
    return { ok: true, json: async () => rows } as unknown as Response;
  });
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchWindow", () => {
  it("asks for one page on the first load", async () => {
    const calls = stubApi(500);

    const rows = await fetchWindow(1, "all", false);

    expect(rows).toHaveLength(PAGE_SIZE);
    expect(calls).toEqual([{ limit: PAGE_SIZE, offset: 0, status: null }]);
  });

  it("splits a window wider than the API ceiling into consecutive pages", async () => {
    // The trap this exists for: the endpoint caps `limit` at 100 and silently
    // returns fewer rows rather than failing, so a single limit=125 request
    // would come back short and read as "no more battles".
    const calls = stubApi(500);
    const pages = 5; // 125 wanted, above MAX_LIMIT

    const rows = await fetchWindow(pages, "all", false);

    expect(rows).toHaveLength(PAGE_SIZE * pages);
    expect(calls).toEqual([
      { limit: MAX_LIMIT, offset: 0, status: null },
      { limit: PAGE_SIZE * pages - MAX_LIMIT, offset: MAX_LIMIT, status: null },
    ]);
    expect(new Set(rows.map((r) => r.id)).size).toBe(rows.length);
  });

  it("stops at the end of the list instead of looping on a short chunk", async () => {
    const calls = stubApi(30);

    const rows = await fetchWindow(4, "all", false);

    expect(rows).toHaveLength(30);
    // 4 pages is exactly MAX_LIMIT, so one request goes out; it comes back
    // with 30 of the 100 asked for, which is the proof the list ended.
    expect(calls).toHaveLength(1);
  });

  it("stops after a short SECOND chunk without asking for a third", async () => {
    const calls = stubApi(120);

    const rows = await fetchWindow(6, "all", false); // 150 wanted

    expect(rows).toHaveLength(120);
    expect(calls).toEqual([
      { limit: MAX_LIMIT, offset: 0, status: null },
      { limit: 50, offset: MAX_LIMIT, status: null },
    ]);
  });

  it("carries the status filter into every page", async () => {
    const calls = stubApi(500);

    await fetchWindow(5, "completed", false);

    expect(calls.map((c) => c.status)).toEqual(["completed", "completed"]);
  });
});
