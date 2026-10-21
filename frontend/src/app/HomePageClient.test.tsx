// @vitest-environment jsdom
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => ({ refresh: vi.fn() }) }));

import { renderToString } from "react-dom/server";
import HomePageClient, { HomePageInitialData } from "./HomePageClient";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";

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

it.each(["en", "ru"] as const)("renders the footer year without digit grouping in %s", locale => {
  const html = renderToString(
    <LocaleProvider initialLocale={locale}><HomePageClient initialData={EMPTY_DATA} /></LocaleProvider>,
  );
  const year = String(new Date().getFullYear());
  expect(html).toContain(year);
  expect(html).not.toMatch(new RegExp(`${year[0]}[,\\s\\u00a0\\u202f]${year.slice(1)}`));
});

it("translates the status terminal rows and hides decorative window titles from assistive technology in ru", () => {
  const html = renderToString(
    <LocaleProvider initialLocale="ru"><HomePageClient initialData={EMPTY_DATA} /></LocaleProvider>,
  );
  for (const raw of ["agents.active", "projects.total", "commits.count", "deploys.live"]) expect(html).not.toContain(raw);
  expect(html).toMatch(/<span aria-hidden="true"[^>]*>platform:\/\/status<\/span>/);
});

it.each(['en', 'ru'] as const)('labels imported activity and links its real GitHub source in %s', locale => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
  const history = { ...EMPTY_DATA, activity: [{ agent_id: null, agent_name: 'Unknown author', action_type: 'code_commit', description: 'Historical change', ts: '2026-01-01T00:00:00Z', metadata: { source: 'github_public_history', github_url: 'https://github.com/Example/repo/commit/abc' } }] };
  const { container } = render(<LocaleProvider initialLocale={locale}><HomePageClient initialData={history} /></LocaleProvider>);
  expect(container.textContent).toContain(locale === 'en' ? 'Imported history' : 'Восстановленная история');
  expect(container.textContent).toContain(locale === 'en' ? 'Unknown author' : 'Автор неизвестен');
  expect(container.querySelector('a[href="https://github.com/Example/repo/commit/abc"]')).not.toBeNull();
  expect(container.querySelector('a[href="/agents/null"]')).toBeNull();
});
