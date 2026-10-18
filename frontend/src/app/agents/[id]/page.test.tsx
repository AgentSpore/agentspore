// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgentPage from "./page";

vi.mock("next/navigation", () => ({ useParams: () => ({ id: "demo-agent" }) }));
vi.mock("@/components/Header", () => ({ Header: () => null }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function stubAgentFetch() {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const agent = {
      id: "demo-agent", name: "Demo Agent", handle: "demo", specialization: "programmer",
      karma: 0, projects_created: 0, code_commits: 0, reviews_done: 0,
      created_at: "2026-10-01T00:00:00Z", last_heartbeat: null, is_active: true,
    };
    const body = input.endsWith("/api/v1/agents/demo-agent") ? agent
      : input.includes("/blog/") ? { posts: [], total: 0, limit: 10, offset: 0 }
      : [];
    return new Response(JSON.stringify(body), { status: 200 });
  }));
}

async function clickCopy() {
  render(<AgentPage />);
  const button = await screen.findByRole("button", { name: "Copy ID" });
  expect(screen.queryByText("More actions")).toBeNull();
  fireEvent.click(button);
}

describe("Agent page copy-id button", () => {
  it("shows Copied after a successful write", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    stubAgentFetch();
    await clickCopy();
    expect(writeText).toHaveBeenCalledWith("demo-agent");
    expect((await screen.findAllByText("Copied")).length).toBeGreaterThan(0);
  });

  it("shows an error when writeText rejects", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn(async () => { throw new Error("denied"); }) } });
    stubAgentFetch();
    await clickCopy();
    expect((await screen.findAllByText("Copy failed")).length).toBeGreaterThan(0);
  });

  it("shows an error when the clipboard API is missing", async () => {
    vi.stubGlobal("navigator", {});
    stubAgentFetch();
    await clickCopy();
    expect((await screen.findAllByText("Copy failed")).length).toBeGreaterThan(0);
  });
});
