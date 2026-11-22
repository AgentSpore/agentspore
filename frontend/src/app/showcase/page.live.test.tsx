// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ShowcasePage from "./page";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock("@/components/Header", () => ({ Header: () => null }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const base = { tech_stack: [], agent_handle: "demo", status: "deployed", description: "Synthetic fixture" };
const PROBE = { service_ok_at: "2026-11-22T10:00:00Z" };
const live = (id: string, title: string, slug: string, over = {}) => ({
  ...base, ...PROBE, service_live: true, id, title, repo_url: `https://github.com/Example/${slug}`, deploy_url: `https://${slug}.live.example.test`, ...over,
});
const dead = (id: string, title: string, slug: string, over = {}) => ({
  ...base, service_live: false, service_ok_at: null, id, title, repo_url: `https://github.com/Example/${slug}`, deploy_url: `https://${slug}.dead.example.test`, ...over,
});

async function open(projects: unknown[], locale: "en" | "ru" = "en") {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(projects), { status: 200 })));
  render(<LocaleProvider initialLocale={locale}><ShowcasePage /></LocaleProvider>);
  await screen.findByRole("heading", { name: locale === "en" ? "Project archive" : "Архив проектов" });
  await screen.findByText(/\d+ (archived projects|проектов в архиве)/);
}
function section(name: string) {
  const el = screen.getByRole("heading", { name }).closest("section");
  if (!el) throw new Error(`Missing section ${name}`);
  return within(el);
}

describe("Showcase live services", () => {
  const projects = [
    live("1", "Pod live", "podmemory"),
    dead("2", "Otkrytka dead", "otkrytka"),
    live("3", "Fresh Tool", "freshtool", { description: "Сервис на русском" }),
    dead("4", "Old Tool", "oldtool"),
  ];

  it("shows a live curated service in Start here and a non-live curated one only in the archive", async () => {
    await open(projects);
    const start = section("Start here"), archive = section("Project archive");
    expect(start.getByRole("heading", { name: "PodMemory" })).toBeTruthy();
    expect(start.queryByRole("heading", { name: "Otkrytka" })).toBeNull();
    expect(start.queryByRole("heading", { name: /Otkrytka dead/ })).toBeNull();
    expect(archive.getByRole("heading", { name: /^Otkrytka dead/ })).toBeTruthy();
    expect(archive.queryByRole("heading", { name: "PodMemory" })).toBeNull();
  });

  it("renders a live project without curated copy as a generic card with its demo link", async () => {
    await open(projects);
    const start = section("Start here");
    expect(start.getByRole("link", { name: "Fresh Tool: live demo" }).getAttribute("href")).toBe("https://freshtool.live.example.test");
    expect(start.getByText("Сервис на русском")).toBeTruthy();
  });

  it("keeps live-demo links out of the archive and states it is source only", async () => {
    await open(projects);
    const archive = section("Project archive");
    expect(archive.queryAllByRole("link", { name: /live demo/ })).toHaveLength(0);
    expect(document.querySelector('a[href*="dead.example.test"]')).toBeNull();
    expect(archive.getByRole("link", { name: "Old Tool: repo" }).getAttribute("href")).toBe("https://github.com/Example/oldtool");
    expect(archive.getByText(/Source code only\. These projects are not running now\./)).toBeTruthy();
  });

  it("renders no project twice", async () => {
    await open(projects);
    const titles = screen.getAllByRole("heading", { level: 3 }).map(h => h.textContent?.toLowerCase());
    expect(new Set(titles).size).toBe(titles.length);
  });

  it("shows no curated service in Start here when nothing is live", async () => {
    await open([dead("1", "Pod dead", "podmemory")]);
    const start = section("Start here");
    expect(start.queryAllByRole("article")).toHaveLength(0);
    expect(start.getByText(/No services are running right now/)).toBeTruthy();
  });

  it("falls back to the curated services when no item carries the live field", async () => {
    await open([{ ...base, id: "9", title: "Legacy", repo_url: "https://github.com/Example/legacy", deploy_url: null }]);
    const start = section("Start here");
    for (const name of ["PodMemory", "Otkrytka", "Pereklichka", "SaaSCalc"]) expect(start.getByRole("heading", { name })).toBeTruthy();
  });

  it("capitalises the breadcrumb root", async () => {
    await open(projects);
    expect(screen.getByRole("link", { name: "Home" }).getAttribute("href")).toBe("/");
    cleanup();
    await open(projects, "ru");
    expect(screen.getByRole("link", { name: "Главная" })).toBeTruthy();
  });

  it("shows an other-language description instead of a placeholder", async () => {
    await open([dead("4", "Vibe", "vibecheck", { description: "Сервис для проверки настроения" })]);
    expect(screen.getByText("Сервис для проверки настроения")).toBeTruthy();
    expect(screen.queryByText(/in another language/)).toBeNull();
  });
});
