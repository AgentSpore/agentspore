// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ProjectPage from "./page";

vi.mock("next/navigation", () => ({ useParams: () => ({ id: "demo-project" }) }));
vi.mock("@/components/Header", () => ({ Header: () => null }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function mockProject(status: string, deployUrl: string | null) {
  const project = {
    id: "demo-project", title: "Example Project", description: "Synthetic demo fixture",
    category: "tools", status, repo_url: "https://github.com/Example/demo",
    deploy_url: deployUrl, tech_stack: [], agent_name: "Demo", agent_handle: "demo",
    creator_agent_id: "demo-agent", votes_up: 0, votes_down: 0,
    created_at: "2026-10-01T00:00:00Z",
  };
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const body = input.endsWith("/ownership") ? null
      : input.endsWith("/contributors") ? { contributors: [] } : project;
    return new Response(JSON.stringify(body), { status: 200 });
  }));
}

describe("Project demo link", () => {
  it.each([
    ["archived", "https://old.example.test"],
    ["active", null],
  ])("hides Demo for %s with deploy URL %s", async (status, url) => {
    mockProject(status, url);
    render(<ProjectPage />);
    await screen.findByRole("heading", { name: "Example Project" });
    expect(screen.queryByRole("link", { name: "Demo" })).toBeNull();
    expect(screen.getByRole("link", { name: "GitHub" }).getAttribute("href"))
      .toBe("https://github.com/Example/demo");
  });

  it("uses the real deployment URL instead of the project title", async () => {
    mockProject("deployed", "https://actual.example.test/app");
    render(<ProjectPage />);
    const link = await screen.findByRole("link", { name: "Demo" });
    expect(link.getAttribute("href")).toBe("https://actual.example.test/app");
  });
});
