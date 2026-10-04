// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
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
