// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { HomeServices } from "./HomeServices";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import type { HomeServicesData } from "@/lib/homeServices";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

const podmemory = { id: "00000000-0000-4000-8000-000000000001", title: "API PodMemory", description: "A broad promise from API", status: "deployed", repo_url: "https://github.com/AgentSpore/podmemory", deploy_url: "https://podmemory.agentspore.com" };
const pereklichka = { ...podmemory, id: "00000000-0000-4000-8000-000000000002", title: "API Pereklichka", repo_url: "https://github.com/AgentSpore/pereklichka", deploy_url: "https://t.me/PereklichkaAppBot" };
const ready: HomeServicesData = { status: "ready", projects: [podmemory, pereklichka] };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("real homepage service cards", () => {
  it.each(["en", "ru"] as const)("uses curated purpose and guide links from actual API projects in %s", locale => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<LocaleProvider initialLocale={locale}><HomeServices initialData={ready} /></LocaleProvider>);
    const cards = screen.getAllByRole("article");
    expect(cards).toHaveLength(2);
    expect(cards[0]?.textContent).toMatch(/backup|резервная копия/);
    expect(screen.queryByText("A broad promise from API")).toBeNull();
    expect(screen.getByRole("link", { name: locale === "en" ? "Read setup guide" : "Прочитать инструкцию" }).getAttribute("href")).toContain("/docs/user-guide.md");
    expect(screen.queryByRole("heading", { name: "SaaSCalc" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Otkrytka" })).toBeNull();
    expect(within(cards[0]!).getByRole("link", { name: locale === "en" ? "About the project" : "О проекте" }).getAttribute("href")).toBe(`/projects/${podmemory.id}`);
    for (const link of document.querySelectorAll('a[target="_blank"]')) expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });
  it("renders the SSR catalog immediately without curated-only services", () => {
    const markup = renderToStaticMarkup(<HomeServices initialData={{ status: "ready", projects: [podmemory] }} />);
    expect(markup).toContain("PodMemory"); expect(markup).not.toContain("Pereklichka");
  });
  it("excludes history, archived entries and dangerous links", () => {
    const markup = renderToStaticMarkup(<HomeServices initialData={{ status: "ready", projects: [{ ...podmemory, status: "historical" }, { ...pereklichka, status: "archived" }, { ...podmemory, deploy_url: "javascript:alert(1)" }] }} />);
    expect(markup).not.toContain("<article"); expect(markup).not.toContain("javascript:"); expect(markup).toContain("No deployed services");
  });
  it("refreshes the public project endpoint and aborts when unmounted", async () => {
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => new Response(JSON.stringify([podmemory])));
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<HomeServices />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "PodMemory" })).toBeTruthy());
    const request = fetchMock.mock.calls[0];
    expect(request?.[0]).toBe("http://localhost:8000/api/v1/projects?limit=200");
    const signal = request?.[1]?.signal;
    expect(signal?.aborted).toBe(false); view.unmount(); expect(signal?.aborted).toBe(true);
  });
  it.each([{}, [{ title: "bad" }]])("treats malformed response as error, not zero demand %j", async payload => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(payload))));
    render(<HomeServices />);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Couldn't read"));
    expect(screen.queryByText(/No deployed services/)).toBeNull();
  });
  it("retains received cards with a stale notice after refresh failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
    render(<HomeServices initialData={ready} />);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("last received"));
    expect(screen.getAllByRole("article")).toHaveLength(2);
  });
  it.each(["loading", "error", "ready"] as const)("distinguishes initial %s without availability claims", status => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    const view = render(<HomeServices initialData={{ status, projects: [] }} />);
    expect(screen.getByRole("status").textContent).toContain(status === "loading" ? "Loading" : status === "error" ? "Couldn't read" : "No deployed");
    expect(view.container.textContent).not.toMatch(/System operational|users online|earned/);
  });
});
