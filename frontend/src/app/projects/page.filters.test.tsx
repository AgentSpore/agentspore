// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ProjectsPage from "./page";
import { type Project } from "@/lib/api";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { docsMessages } from "@/lib/i18n/docs";
import { displayPublicLabel, publicMessages } from "@/lib/i18n/public";

vi.mock("next/navigation", () => ({ usePathname: () => "/projects", useRouter: () => ({ refresh: vi.fn() }) }));
const PROJECT: Project = {
  id: "synthetic-project", title: "Synthetic project", description: "Synthetic description",
  category: "productivity", status: "deployed", votes_up: 0, votes_down: 0, score: 0,
  repo_url: null, deploy_url: null, tech_stack: [], hackathon_id: null,
  creator_agent_id: "synthetic-agent", agent_name: "Synthetic agent", agent_handle: "synthetic-agent",
  github_stars: 0, created_at: "2026-10-15T00:00:00Z",
};
beforeEach(() => {
  localStorage.clear();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function serveProjects(empty = false) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "https://example.invalid");
    if (url.pathname !== "/api/v1/projects") throw new Error("Unexpected synthetic request");
    const scoped = url.searchParams.has("category") || url.searchParams.has("status");
    return new Response(JSON.stringify(empty || scoped ? [] : [PROJECT]));
  }));
}

describe.each(["en", "ru"] as const)("project empty results in %s", locale => {
  it.each(["category", "status"] as const)("keeps the %s reset available after an empty server response", async filter => {
    serveProjects();
    render(<LocaleProvider initialLocale={locale}><ProjectsPage /></LocaleProvider>);
    await screen.findByRole("link", { name: PROJECT.title });
    const label = filter === "category" ? docsMessages[locale].projectCategoryFilter : docsMessages[locale].projectStatusFilter;
    const option = displayPublicLabel(locale, filter === "category" ? "fintech" : "building");
    fireEvent.click(within(screen.getByRole("group", { name: label })).getByRole("button", { name: option }));
    await waitFor(() => expect(screen.queryByRole("link", { name: PROJECT.title })).toBeNull());
    const group = screen.getByRole("group", { name: label });
    expect(within(group).getByRole("button", { name: option }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText(publicMessages[locale].noProjectsFound)).toBeTruthy();
    expect(screen.queryByText(docsMessages[locale].eProjectsTitle)).toBeNull();
    fireEvent.click(within(group).getByRole("button", { name: displayPublicLabel(locale, "all") }));
    expect(await screen.findByRole("link", { name: PROJECT.title })).toBeTruthy();
    expect(within(screen.getByRole("group", { name: label })).getByRole("button", { name: displayPublicLabel(locale, "all") }).getAttribute("aria-pressed")).toBe("true");
  });

  it("teaches and hides controls only when the unfiltered server list is empty", async () => {
    serveProjects(true);
    render(<LocaleProvider initialLocale={locale}><ProjectsPage /></LocaleProvider>);
    expect(await screen.findByText(docsMessages[locale].eProjectsTitle)).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("group", { name: docsMessages[locale].projectCategoryFilter })).toBeNull();
    expect(screen.getAllByRole("link", { name: docsMessages[locale].eAction }).some(link => link.getAttribute("href") === "/docs/connect")).toBe(true);
  });

  it("keeps controls and the filtered message for an unmatched local search", async () => {
    serveProjects();
    render(<LocaleProvider initialLocale={locale}><ProjectsPage /></LocaleProvider>);
    await screen.findByRole("link", { name: PROJECT.title });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "No such project" } });
    expect(screen.getByText(publicMessages[locale].noProjectsFound)).toBeTruthy();
    expect(screen.queryByText(docsMessages[locale].eProjectsTitle)).toBeNull();
    expect(screen.getByRole("group", { name: docsMessages[locale].projectCategoryFilter })).toBeTruthy();
  });
});
