// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ShowcasePage from "./page";

vi.mock("@/components/Header", () => ({ Header: () => null }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function mockProjects() {
  const base = { description: "Synthetic demo fixture", tech_stack: [], agent_handle: "demo" };
  const projects = [
    { ...base, id: "1", title: "SignSafe", repo_url: "https://github.com/Example/signsafe", status: "archived", deploy_url: "https://old.example.test" },
    { ...base, id: "2", title: "Archived Verdict", repo_url: "https://github.com/Example/verdict", status: "archived", deploy_url: "https://old.example.test" },
    { ...base, id: "3", title: "No URL", repo_url: "https://github.com/Example/otkrytka", status: "active", deploy_url: null },
    { ...base, id: "4", title: "Live Verdict", repo_url: "https://github.com/Example/verdict", status: "deployed", deploy_url: "https://actual.example.test/app" },
  ];
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(projects), { status: 200 })));
}

describe("Showcase demo links", () => {
  it("withdraws SignSafe from the curated showcase", async () => {
    mockProjects();
    render(<ShowcasePage />);
    await screen.findByRole("heading", { name: "Live Verdict" });
    expect(screen.queryByRole("heading", { name: "SignSafe" })).toBeNull();
  });

  it("hides archived or missing deployments and preserves the real live URL", async () => {
    mockProjects();
    render(<ShowcasePage />);
    const heading = await screen.findByRole("heading", { name: "Live Verdict" });
    const card = heading.closest(".project-card");
    if (!(card instanceof HTMLElement)) throw new Error("Live project card is missing");
    expect(screen.getAllByRole("link", { name: "live demo" })).toHaveLength(1);
    expect(within(card).getByRole("link", { name: "live demo" }).getAttribute("href"))
      .toBe("https://actual.example.test/app");
    expect(screen.getAllByRole("link", { name: "repo" })).toHaveLength(3);
  });
});

function featuredCard(name: string) {
  const card = screen.getByRole("heading", { name }).closest("article");
  if (!(card instanceof HTMLElement)) throw new Error("Featured card is missing");
  return within(card);
}

describe("Showcase service selection", () => {
  it("does not promise all catalog services are running today", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<ShowcasePage />);
    expect(screen.queryByText(/running service today/i)).toBeNull();
    expect(screen.getAllByRole("article")).toHaveLength(2);
  });

  it.each([200, 503])("keeps actionable cards with an empty or failed catalog (%s)", async (status) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status })));
    render(<ShowcasePage />);
    await screen.findByText(status === 200 ? /no catalog apps found/i : /catalog unavailable/i);
    const family = featuredCard("Pereklichka");
    for (const name of ["Pereklichka", "SaaSCalc"]) {
      const card = screen.getByRole("heading", { name }).closest("article");
      expect(card?.querySelectorAll("dt")).toHaveLength(5);
      expect(card?.querySelectorAll("dd")).toHaveLength(5);
      expect(Array.from(card?.querySelectorAll("dd") ?? []).every(field => field.textContent?.trim())).toBe(true);
      if (!(card instanceof HTMLElement)) throw new Error("Service card is missing");
      expect(within(card).getByText(/5 October 2026/)).toBeTruthy();
    }

    expect(family.getByRole("link", { name: "Open Telegram bot" }).getAttribute("href"))
      .toBe("https://t.me/PereklichkaAppBot");
    expect(family.getByRole("link", { name: "Guide and evidence" }).getAttribute("href"))
      .toBe("https://github.com/AgentSpore/pereklichka/blob/main/docs/user-guide.md");
    expect(family.getByRole("link", { name: "Privacy policy" }).getAttribute("href"))
      .toBe("https://pereklichka.agentspore.com/privacy");
    expect(family.getByText(/voice conversation.*not independently verified/i)).toBeTruthy();
    expect(family.getByText(/telegram delivery.*not rechecked/i)).toBeTruthy();
    const calc = featuredCard("SaaSCalc");
    expect(calc.getByRole("link", { name: "Open calculator" }).getAttribute("href"))
      .toBe("https://saascalc.agentspore.com");
    expect(calc.getByRole("link", { name: "Source of local checks" }).getAttribute("href"))
      .toBe("https://github.com/AgentSpore/saascalc/tree/48981b19c5430669b6d0236d998fc4e99d372704");
    expect(calc.getByText(/5,000 monthly revenue and 60,000 annual revenue/)).toBeTruthy();
    expect(calc.getByText(/live calculation.*not verified/i)).toBeTruthy();
    expect(calc.getByText(/formula was checked locally/i)).toBeTruthy();
    expect(screen.getAllByRole("link", { name: /@exzentttt/ }).every(link =>
      link.getAttribute("href") === "https://t.me/exzentttt")).toBe(true);
  });

  it("switches page and service instructions to Russian without losing URLs", async () => {
    mockProjects();
    render(<ShowcasePage />);
    await screen.findByRole("heading", { name: "Live Verdict" });
    fireEvent.click(screen.getByRole("button", { name: "Русский" }));
    expect(screen.getByRole("button", { name: "Русский" }).getAttribute("aria-pressed")).toBe("true");
    const family = featuredCard("Перекличка");
    expect(family.getByRole("link", { name: "Открыть Telegram-бота" }).getAttribute("href"))
      .toBe("https://t.me/PereklichkaAppBot");
    expect(family.getByText(/голосовой разговор.*не проверен/i)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Каталог проектов" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "English" }));
    expect(screen.getByRole("heading", { name: "Pereklichka" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "live demo" }).getAttribute("href"))
      .toBe("https://actual.example.test/app");
  });

  it("retains catalog data and service links when a subsequent poll fails, then retries", async () => {
    mockProjects();
    const transport = vi.mocked(fetch);
    render(<ShowcasePage />);
    await screen.findByRole("heading", { name: "Live Verdict" });
    transport.mockResolvedValueOnce(new Response("[]", { status: 503 }));
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(await screen.findByRole("status")).toHaveProperty("textContent", expect.stringContaining("Stale"));
    expect(screen.getByRole("heading", { name: "Live Verdict" })).toBeTruthy();
    expect(featuredCard("Pereklichka").getByRole("link", { name: "Open Telegram bot" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await act(async () => {});
    expect(screen.queryByRole("status")).toBeNull();
    expect(transport).toHaveBeenCalledTimes(3);
  });
});
