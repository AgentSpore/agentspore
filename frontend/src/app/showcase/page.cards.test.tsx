// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ShowcasePage from "./page";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock("@/components/Header", () => ({ Header: () => null }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const base = { tech_stack: [], agent_handle: "demo", status: "deployed", deploy_url: "https://live.example.test/x" };
const PROJECTS = [
  { ...base, service_live: true, id: "1", title: "Live Verdict", description: "A verdict tool", repo_url: "https://github.com/Example/verdict" },
  { ...base, service_live: false, id: "2", title: "Empty Dawn", description: "", repo_url: "https://github.com/Example/dawntask" },
  { ...base, service_live: false, id: "3", title: "Russian Vibe", description: "Сервис для проверки настроения", repo_url: "https://github.com/Example/vibecheck" },
  { ...base, service_live: true, id: "4", title: "Duplicate Otkrytka", description: "Greeting cards", repo_url: "https://github.com/Example/otkrytka" },
  { ...base, service_live: false, id: "5", title: "Repo Only", description: "Only source", repo_url: "https://github.com/Example/reviewray", deploy_url: null },
  { ...base, service_live: true, id: "6", title: "Duplicate Calc", description: "Calculator", repo_url: "https://github.com/Example/saascalc" },
];

async function renderPage(locale: "en" | "ru") {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(PROJECTS), { status: 200 })));
  render(<LocaleProvider initialLocale={locale}><ShowcasePage /></LocaleProvider>);
  await screen.findByRole("heading", { name: /^Live Verdict/ });
}

describe("Showcase cards", () => {
  it("has no numbering band", async () => {
    await renderPage("en");
    const spans = Array.from(document.querySelectorAll("span")).map(span => span.textContent?.trim());
    expect(spans.filter(text => /^0\d\s*\/$/.test(text ?? ""))).toEqual([]);
  });

  it("gives every card exactly one primary link and no nested interactive elements", async () => {
    await renderPage("en");
    const cards = screen.getAllByRole("article");
    expect(cards.length).toBeGreaterThanOrEqual(5);
    for (const card of cards) {
      expect(card.querySelectorAll("a[data-card-link]")).toHaveLength(1);
      expect(card.querySelector("a a, a button, button a")).toBeNull();
    }
  });

  it("points a catalog card at the live demo, else the source", async () => {
    await renderPage("en");
    const hrefOf = (name: string) => screen.getByRole("link", { name }).getAttribute("href");
    expect(hrefOf("Live Verdict: live demo")).toBe("https://live.example.test/x");
    expect(hrefOf("Repo Only: repo")).toBe("https://github.com/Example/reviewray");
  });

  it("does not repeat a featured service in the catalog", async () => {
    await renderPage("en");
    expect(screen.queryByRole("heading", { name: /^Duplicate Otkrytka/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: /^Duplicate Calc/ })).toBeNull();
    const names = screen.getAllByRole("heading", { level: 3 }).map(h => h.textContent?.toLowerCase());
    for (const featured of ["otkrytka", "podmemory", "saascalc"]) {
      expect(names.filter(name => name === featured)).toHaveLength(1);
    }
  });

  it("uses a localized fallback for empty descriptions and shows other-language ones as written", async () => {
    await renderPage("en");
    const emptyCard = screen.getByRole("heading", { name: /^Empty Dawn/ }).closest("article");
    expect(emptyCard?.textContent).toContain("The author has not added a description yet.");
    const ruCard = screen.getByRole("heading", { name: /^Russian Vibe/ }).closest("article");
    expect(ruCard?.textContent).toContain("Сервис для проверки настроения");
  });

  it("renders a fixed-ratio thumbnail slot in every card", async () => {
    await renderPage("ru");
    for (const card of screen.getAllByRole("article")) expect(card.querySelectorAll("[data-thumb]")).toHaveLength(1);
  });
});
