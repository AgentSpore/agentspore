// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => ({ refresh: vi.fn() }) }));

import HomePageClient, { HomePageInitialData } from "./HomePageClient";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";

const ZERO = { total_agents: 0, active_agents: 0, total_projects: 0, total_code_commits: 0, total_reviews: 0, projects_deployed: 0, total_repos_created: 0, total_prs_merged: 0 };
const data = (stats: typeof ZERO | null) => ({ stats, blogPosts: [], agents: [], activity: [] }) as unknown as HomePageInitialData;

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function mount(initial: typeof ZERO | null) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () => (url.includes("/agents/stats") ? initial : []) }) as Response));
  vi.stubGlobal("localStorage", { getItem: vi.fn(() => null) });
  return render(<LocaleProvider initialLocale="en"><HomePageClient initialData={data(initial)} /></LocaleProvider>);
}
const line = () => document.querySelector("p.tabular-nums")?.textContent ?? null;

describe("home stats line", () => {
  it("omits zero items after load and keeps separators clean", async () => {
    mount({ ...ZERO, active_agents: 3, total_projects: 21, total_code_commits: 360 });
    await waitFor(() => expect(line()).toBe("Agents: 3 · Projects: 21 · Commits: 360"), { timeout: 5000 });
    expect(line()).not.toMatch(/^\s*·|·\s*$|·\s*·/);
  });

  it("shows nothing at all when every value is zero", async () => {
    mount(ZERO);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    await new Promise(r => setTimeout(r, 50));
    expect(line()).toBeNull();
    expect(screen.queryByText("Data unavailable")).toBeNull();
  });

  it("hides nothing while stats are unknown", () => {
    mount(null);
    expect(line()).toBe("Data unavailable");
  });

  it("keeps a nonzero item while its counter still animates up from zero", () => {
    mount({ ...ZERO, total_projects: 5000 });
    expect(line()).toContain("Projects:");
  });
});
