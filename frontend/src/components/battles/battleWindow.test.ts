import { afterEach, describe, expect, it, vi } from "vitest";
import { MAX_LIMIT, PAGE_SIZE, fetchWindow } from "./battleWindow";

type Call = { limit: number; offset: number; status: string | null; undecided: string | null };

function stubApi(total: number, failFrom?: number): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", async (url: string) => {
    const params = new URL(url, "http://x").searchParams;
    const limit = Number(params.get("limit"));
    const offset = Number(params.get("offset"));
    calls.push({
      limit,
      offset,
      status: params.get("status"),
      undecided: params.get("include_undecided"),
    });
    if (failFrom !== undefined && calls.length >= failFrom) {
      return { ok: false, status: 503 } as unknown as Response;
    }
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

    const { rows, exhausted } = await fetchWindow(1, "all", false);

    expect(rows).toHaveLength(PAGE_SIZE);
    expect(exhausted).toBe(false);
    expect(calls).toEqual([{ limit: PAGE_SIZE, offset: 0, status: null, undecided: null }]);
  });

  it("splits a window wider than the API ceiling into consecutive pages", async () => {
    // The trap this exists for: the endpoint caps `limit` at 100 and silently
    // returns fewer rows rather than failing, so a single limit=125 request
    // would come back short and read as "no more battles".
    const calls = stubApi(500);
    const pages = 5; // 125 wanted, above MAX_LIMIT

    const { rows } = await fetchWindow(pages, "all", false);

    expect(rows).toHaveLength(PAGE_SIZE * pages);
    expect(calls.map((c) => ({ limit: c.limit, offset: c.offset }))).toEqual([
      { limit: MAX_LIMIT, offset: 0 },
      { limit: PAGE_SIZE * pages - MAX_LIMIT, offset: MAX_LIMIT },
    ]);
    expect(new Set(rows.map((r) => r.id)).size).toBe(rows.length);
  });

  it("reports exhaustion instead of leaving the caller to guess", async () => {
    const calls = stubApi(30);

    const { rows, exhausted } = await fetchWindow(4, "all", false);

    expect(rows).toHaveLength(30);
    expect(exhausted).toBe(true);
    // 4 pages is exactly MAX_LIMIT, so one request goes out and comes back
    // with 30 of the 100 asked for, which is the proof the list ended.
    expect(calls).toHaveLength(1);
  });

  it("does not call a list exactly filling the window exhausted", async () => {
    // The off-by-one a `rows.length >= wanted` caller heuristic gets wrong:
    // a set of exactly 100 fills a 4-page window, and more may sit behind it.
    stubApi(500);

    const { rows, exhausted } = await fetchWindow(4, "all", false);

    expect(rows).toHaveLength(MAX_LIMIT);
    expect(exhausted).toBe(false);
  });

  it("stops after a short SECOND chunk without asking for a third", async () => {
    const calls = stubApi(120);

    const { rows, exhausted } = await fetchWindow(6, "all", false); // 150 wanted

    expect(rows).toHaveLength(120);
    expect(exhausted).toBe(true);
    expect(calls.map((c) => c.offset)).toEqual([0, MAX_LIMIT]);
  });

  it("carries the status filter into every page", async () => {
    const calls = stubApi(500);

    await fetchWindow(5, "completed", false);

    expect(calls.map((c) => c.status)).toEqual(["completed", "completed"]);
  });

  it("carries include_undecided into every page", async () => {
    // Dropping this parameter is invisible in the UI until an undecided
    // battle exists: the toggle stays on while its battles quietly vanish.
    const calls = stubApi(500);

    await fetchWindow(5, "all", true);

    expect(calls.map((c) => c.undecided)).toEqual(["true", "true"]);
  });

  it("omits include_undecided when the toggle is off", async () => {
    const calls = stubApi(500);

    await fetchWindow(1, "all", false);

    expect(calls[0].undecided).toBeNull();
  });

  it("throws when a later page fails rather than returning a torn window", async () => {
    // A partial window would render as a complete one. Surfacing the error
    // keeps the previous good list on screen instead.
    stubApi(500, 2);

    await expect(fetchWindow(5, "all", false)).rejects.toThrow("HTTP 503");
  });
});
