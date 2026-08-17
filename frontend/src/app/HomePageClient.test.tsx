// @vitest-environment jsdom
import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

import HomePageClient, { HomePageInitialData } from "./HomePageClient";

const EMPTY_DATA: HomePageInitialData = {
  stats: null,
  blogPosts: [],
  agents: [],
  activity: [],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

// The homepage's client-side refresh must call a route the backend actually
// serves — /agents/list 404s in production (no such route), while
// /agents/leaderboard exists and returns the same AgentProfile[] shape.
describe("HomePageClient client-side refresh", () => {
  it("fetches the agent leaderboard route, not the removed /agents/list route", async () => {
    const fetchMock = vi.fn(async (url: string) => { void url; return { ok: true, status: 200, json: async () => [] } as Response; });
    vi.stubGlobal("fetch", fetchMock);

    render(<HomePageClient initialData={EMPTY_DATA} />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const calledUrls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(calledUrls.some((u) => u.includes("/api/v1/agents/leaderboard"))).toBe(true);
    expect(calledUrls.some((u) => u.includes("/api/v1/agents/list"))).toBe(false);
  });
});
