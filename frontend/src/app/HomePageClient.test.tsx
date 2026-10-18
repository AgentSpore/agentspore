// @vitest-environment jsdom
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => ({ refresh: vi.fn() }) }));

import { renderToString } from "react-dom/server";
import HomePageClient, { HomePageInitialData } from "./HomePageClient";

const EMPTY_DATA: HomePageInitialData = {
  stats: null,
  blogPosts: [],
  agents: [],
  activity: [],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// The homepage's client-side refresh must call a route the backend actually
// serves — /agents/list 404s in production (no such route), while
// /agents/leaderboard exists and returns the same AgentProfile[] shape.
describe("HomePageClient client-side refresh", () => {
  it("fetches the agent leaderboard route, not the removed /agents/list route", async () => {
    const fetchMock = vi.fn(async (url: string) => { return { ok: true, status: 200, json: async () => url.includes("/agents/stats") ? { total_agents: 0, active_agents: 0, total_projects: 0, total_code_commits: 0, total_reviews: 0, projects_deployed: 0, total_repos_created: 0, total_prs_merged: 0 } : [] } as Response; });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("localStorage", { getItem: vi.fn(() => null) });

    render(<HomePageClient initialData={EMPTY_DATA} />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const calledUrls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(calledUrls.some((u) => u.includes("/api/v1/agents/leaderboard"))).toBe(true);
    expect(calledUrls.some((u) => u.includes("/api/v1/agents/list"))).toBe(false);
  });
});

it("hydrates empty server particles and retains client particles across rerenders", async () => {
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  vi.stubGlobal("localStorage", { getItem: vi.fn(() => null) });
  const element = <HomePageClient initialData={EMPTY_DATA} />;
  const container = document.createElement("div");
  document.body.append(container);
  container.innerHTML = renderToString(element);
  expect(container.querySelectorAll(".particle-float")).toHaveLength(0);
  const onRecoverableError = vi.fn();
  const view = render(element, { container, hydrate: true, onRecoverableError });
  await waitFor(() => expect(container.querySelectorAll(".particle-float")).toHaveLength(40));
  const styles = Array.from(container.querySelectorAll(".particle-float"), particle => particle.getAttribute("style"));
  view.rerender(<HomePageClient initialData={{ ...EMPTY_DATA }} />);
  expect(Array.from(container.querySelectorAll(".particle-float"), particle => particle.getAttribute("style"))).toEqual(styles);
  expect(onRecoverableError).not.toHaveBeenCalled();
  view.unmount();
  container.remove();
});
